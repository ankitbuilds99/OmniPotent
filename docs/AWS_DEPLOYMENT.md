# AWS Deployment Guide

This guide covers deploying DevOps Copilot on AWS using various services. Choose the deployment option that best fits your needs:

| Option | Best For | Estimated Cost |
|--------|----------|----------------|
| ECS Fargate | Production, auto-scaling | ~$50-200/month |
| EKS | Large teams, K8s native | ~$150-500/month |
| EC2 | Simple, predictable | ~$30-100/month |
| Lambda + API Gateway | Low traffic, cost optimization | ~$5-50/month |

## Prerequisites

- AWS CLI v2 installed and configured
- Docker installed locally
- AWS account with appropriate permissions
- Domain name (optional, for HTTPS)

```bash
# Verify AWS CLI
aws --version
aws sts get-caller-identity
```

## Option 1: ECS Fargate (Recommended)

ECS Fargate provides serverless container orchestration with automatic scaling.

### 1.1 Create ECR Repository

```bash
# Create ECR repository
aws ecr create-repository \
    --repository-name devops-copilot \
    --image-scanning-configuration scanOnPush=true \
    --region us-west-2

# Get ECR login
aws ecr get-login-password --region us-west-2 | \
    docker login --username AWS --password-stdin \
    <account-id>.dkr.ecr.us-west-2.amazonaws.com
```

### 1.2 Build and Push Docker Image

```bash
# Build the image
docker build -t devops-copilot:latest .

# Tag for ECR
docker tag devops-copilot:latest \
    <account-id>.dkr.ecr.us-west-2.amazonaws.com/devops-copilot:latest

# Push to ECR
docker push <account-id>.dkr.ecr.us-west-2.amazonaws.com/devops-copilot:latest
```

### 1.3 Create VPC and Networking

```bash
# Create VPC (or use existing)
aws ec2 create-vpc --cidr-block 10.0.0.0/16 --tag-specifications \
    'ResourceType=vpc,Tags=[{Key=Name,Value=devops-copilot-vpc}]'

# Create subnets (at least 2 for ALB)
aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.0.1.0/24 \
    --availability-zone us-west-2a
aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.0.2.0/24 \
    --availability-zone us-west-2b
```

### 1.4 Create ECS Cluster

```bash
# Create cluster
aws ecs create-cluster --cluster-name devops-copilot --capacity-providers FARGATE

# Create task execution role (for pulling images, logging)
aws iam create-role --role-name ecsTaskExecutionRole --assume-role-policy-document '{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}'

aws iam attach-role-policy --role-name ecsTaskExecutionRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

### 1.5 Store Secrets in AWS Secrets Manager

```bash
# Create secret for API keys
aws secretsmanager create-secret \
    --name devops-copilot/api-keys \
    --secret-string '{
        "COPILOT_API_TOKEN": "your-secure-token",
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "DATADOG_API_KEY": "...",
        "DATADOG_APP_KEY": "...",
        "GITHUB_TOKEN": "ghp_...",
        "PAGERDUTY_API_KEY": "..."
    }'
```

### 1.6 Create Task Definition

Create `task-definition.json`:

```json
{
  "family": "devops-copilot",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::<account-id>:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::<account-id>:role/ecsTaskRole",
  "containerDefinitions": [{
    "name": "devops-copilot",
    "image": "<account-id>.dkr.ecr.us-west-2.amazonaws.com/devops-copilot:latest",
    "portMappings": [{
      "containerPort": 8000,
      "protocol": "tcp"
    }],
    "environment": [
      {"name": "COPILOT_PROVIDER", "value": "anthropic"},
      {"name": "COPILOT_LOG_FORMAT", "value": "json"},
      {"name": "COPILOT_RATE_LIMIT_PER_MIN", "value": "60"}
    ],
    "secrets": [
      {
        "name": "COPILOT_API_TOKEN",
        "valueFrom": "arn:aws:secretsmanager:us-west-2:<account-id>:secret:devops-copilot/api-keys:COPILOT_API_TOKEN::"
      },
      {
        "name": "ANTHROPIC_API_KEY",
        "valueFrom": "arn:aws:secretsmanager:us-west-2:<account-id>:secret:devops-copilot/api-keys:ANTHROPIC_API_KEY::"
      }
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/devops-copilot",
        "awslogs-region": "us-west-2",
        "awslogs-stream-prefix": "ecs"
      }
    },
    "healthCheck": {
      "command": ["CMD-SHELL", "curl -f http://localhost:8000/healthz || exit 1"],
      "interval": 30,
      "timeout": 5,
      "retries": 3,
      "startPeriod": 60
    }
  }]
}
```

```bash
# Register task definition
aws ecs register-task-definition --cli-input-json file://task-definition.json
```

### 1.7 Create Application Load Balancer

```bash
# Create security group for ALB
aws ec2 create-security-group \
    --group-name devops-copilot-alb-sg \
    --description "ALB security group" \
    --vpc-id <vpc-id>

# Allow HTTPS traffic
aws ec2 authorize-security-group-ingress \
    --group-id <alb-sg-id> \
    --protocol tcp --port 443 --cidr 0.0.0.0/0

# Create ALB
aws elbv2 create-load-balancer \
    --name devops-copilot-alb \
    --subnets <subnet-1> <subnet-2> \
    --security-groups <alb-sg-id> \
    --type application

# Create target group
aws elbv2 create-target-group \
    --name devops-copilot-tg \
    --protocol HTTP \
    --port 8000 \
    --vpc-id <vpc-id> \
    --target-type ip \
    --health-check-path /healthz
```

### 1.8 Create ECS Service

```bash
# Create security group for ECS tasks
aws ec2 create-security-group \
    --group-name devops-copilot-ecs-sg \
    --description "ECS tasks security group" \
    --vpc-id <vpc-id>

# Allow traffic from ALB
aws ec2 authorize-security-group-ingress \
    --group-id <ecs-sg-id> \
    --protocol tcp --port 8000 \
    --source-group <alb-sg-id>

# Create service
aws ecs create-service \
    --cluster devops-copilot \
    --service-name devops-copilot-service \
    --task-definition devops-copilot \
    --desired-count 2 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[<subnet-1>,<subnet-2>],securityGroups=[<ecs-sg-id>],assignPublicIp=ENABLED}" \
    --load-balancers "targetGroupArn=<target-group-arn>,containerName=devops-copilot,containerPort=8000"
```

### 1.9 Configure Auto Scaling

```bash
# Register scalable target
aws application-autoscaling register-scalable-target \
    --service-namespace ecs \
    --resource-id service/devops-copilot/devops-copilot-service \
    --scalable-dimension ecs:service:DesiredCount \
    --min-capacity 1 \
    --max-capacity 10

# Create scaling policy (target CPU 70%)
aws application-autoscaling put-scaling-policy \
    --service-namespace ecs \
    --resource-id service/devops-copilot/devops-copilot-service \
    --scalable-dimension ecs:service:DesiredCount \
    --policy-name cpu-scaling \
    --policy-type TargetTrackingScaling \
    --target-tracking-scaling-policy-configuration '{
        "TargetValue": 70.0,
        "PredefinedMetricSpecification": {
            "PredefinedMetricType": "ECSServiceAverageCPUUtilization"
        },
        "ScaleInCooldown": 300,
        "ScaleOutCooldown": 60
    }'
```

---

## Option 2: EKS (Kubernetes)

For teams already using Kubernetes.

### 2.1 Create EKS Cluster

```bash
# Using eksctl (recommended)
eksctl create cluster \
    --name devops-copilot \
    --region us-west-2 \
    --nodegroup-name workers \
    --node-type t3.medium \
    --nodes 2 \
    --nodes-min 1 \
    --nodes-max 5 \
    --managed
```

### 2.2 Kubernetes Manifests

Create `k8s/deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: devops-copilot
  labels:
    app: devops-copilot
spec:
  replicas: 2
  selector:
    matchLabels:
      app: devops-copilot
  template:
    metadata:
      labels:
        app: devops-copilot
    spec:
      containers:
      - name: devops-copilot
        image: <account-id>.dkr.ecr.us-west-2.amazonaws.com/devops-copilot:latest
        ports:
        - containerPort: 8000
        env:
        - name: COPILOT_PROVIDER
          value: "anthropic"
        - name: COPILOT_LOG_FORMAT
          value: "json"
        envFrom:
        - secretRef:
            name: devops-copilot-secrets
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /readyz
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: devops-copilot
spec:
  selector:
    app: devops-copilot
  ports:
  - port: 80
    targetPort: 8000
  type: LoadBalancer
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: devops-copilot
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: devops-copilot
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

### 2.3 Create Secrets

```bash
# Create Kubernetes secret from AWS Secrets Manager
kubectl create secret generic devops-copilot-secrets \
    --from-literal=COPILOT_API_TOKEN='your-token' \
    --from-literal=ANTHROPIC_API_KEY='sk-ant-...' \
    --from-literal=DATADOG_API_KEY='...'
```

### 2.4 Deploy

```bash
kubectl apply -f k8s/deployment.yaml
```

---

## Option 3: EC2 (Simple)

For predictable workloads or development.

### 3.1 Launch EC2 Instance

```bash
# Create key pair
aws ec2 create-key-pair --key-name devops-copilot-key \
    --query 'KeyMaterial' --output text > devops-copilot-key.pem
chmod 400 devops-copilot-key.pem

# Launch instance
aws ec2 run-instances \
    --image-id ami-0c55b159cbfafe1f0 \
    --instance-type t3.medium \
    --key-name devops-copilot-key \
    --security-group-ids <sg-id> \
    --user-data file://user-data.sh \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=devops-copilot}]'
```

### 3.2 User Data Script

Create `user-data.sh`:

```bash
#!/bin/bash
set -e

# Install Docker
yum update -y
yum install -y docker
systemctl start docker
systemctl enable docker
usermod -a -G docker ec2-user

# Install Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
    -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Pull and run
docker pull <account-id>.dkr.ecr.us-west-2.amazonaws.com/devops-copilot:latest

docker run -d \
    --name devops-copilot \
    --restart unless-stopped \
    -p 8000:8000 \
    -e COPILOT_PROVIDER=anthropic \
    -e COPILOT_API_TOKEN=$COPILOT_API_TOKEN \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    <account-id>.dkr.ecr.us-west-2.amazonaws.com/devops-copilot:latest
```

---

## Database Setup (RDS PostgreSQL)

For production multi-tenant deployments:

```bash
# Create RDS instance
aws rds create-db-instance \
    --db-instance-identifier devops-copilot-db \
    --db-instance-class db.t3.micro \
    --engine postgres \
    --engine-version 15 \
    --master-username copilot_admin \
    --master-user-password <secure-password> \
    --allocated-storage 20 \
    --vpc-security-group-ids <db-sg-id> \
    --db-subnet-group-name <subnet-group> \
    --storage-encrypted \
    --backup-retention-period 7
```

Set the database URL in your environment:

```bash
DATABASE_URL=postgresql://copilot_admin:<password>@<rds-endpoint>:5432/devops_copilot
```

---

## Monitoring and Logging

### CloudWatch Logs

Logs are automatically sent to CloudWatch when using ECS. Create log group:

```bash
aws logs create-log-group --log-group-name /ecs/devops-copilot
aws logs put-retention-policy --log-group-name /ecs/devops-copilot --retention-in-days 30
```

### CloudWatch Alarms

```bash
# High CPU alarm
aws cloudwatch put-metric-alarm \
    --alarm-name devops-copilot-high-cpu \
    --metric-name CPUUtilization \
    --namespace AWS/ECS \
    --statistic Average \
    --period 300 \
    --threshold 80 \
    --comparison-operator GreaterThanThreshold \
    --dimensions Name=ClusterName,Value=devops-copilot Name=ServiceName,Value=devops-copilot-service \
    --evaluation-periods 2 \
    --alarm-actions <sns-topic-arn>

# 5xx errors alarm
aws cloudwatch put-metric-alarm \
    --alarm-name devops-copilot-5xx-errors \
    --metric-name HTTPCode_Target_5XX_Count \
    --namespace AWS/ApplicationELB \
    --statistic Sum \
    --period 60 \
    --threshold 10 \
    --comparison-operator GreaterThanThreshold \
    --dimensions Name=LoadBalancer,Value=<alb-arn> \
    --evaluation-periods 1 \
    --alarm-actions <sns-topic-arn>
```

### Prometheus Metrics

The `/metrics/slo` endpoint exposes Prometheus metrics. Configure scraping:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'devops-copilot'
    static_configs:
      - targets: ['devops-copilot.internal:8000']
    metrics_path: /metrics/slo
    scrape_interval: 30s
```

---

## Security Best Practices

### 1. Network Security

```bash
# Restrict security group to VPC only
aws ec2 authorize-security-group-ingress \
    --group-id <ecs-sg-id> \
    --protocol tcp --port 8000 \
    --cidr 10.0.0.0/16

# Enable VPC Flow Logs
aws ec2 create-flow-logs \
    --resource-type VPC \
    --resource-ids <vpc-id> \
    --traffic-type ALL \
    --log-destination-type cloud-watch-logs \
    --log-group-name vpc-flow-logs
```

### 2. IAM Least Privilege

Create a task role with minimal permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:*:*:secret:devops-copilot/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:log-group:/ecs/devops-copilot:*"
    }
  ]
}
```

### 3. Enable WAF (Web Application Firewall)

```bash
aws wafv2 create-web-acl \
    --name devops-copilot-waf \
    --scope REGIONAL \
    --default-action Allow={} \
    --rules file://waf-rules.json \
    --visibility-config SampledRequestsEnabled=true,CloudWatchMetricsEnabled=true,MetricName=devops-copilot
```

---

## Cost Optimization

| Service | Monthly Estimate | Optimization Tips |
|---------|-----------------|-------------------|
| ECS Fargate | $30-50 | Use Spot capacity for non-prod |
| ALB | $20-30 | Use single ALB for multiple services |
| RDS | $15-30 | Use db.t3.micro for dev, Reserved Instances for prod |
| Secrets Manager | $1-5 | Combine secrets into single JSON |
| CloudWatch | $5-20 | Reduce log retention, use sampling |

### Fargate Spot

For cost savings (up to 70% off):

```json
{
  "capacityProviderStrategy": [
    {
      "capacityProvider": "FARGATE_SPOT",
      "weight": 2,
      "base": 0
    },
    {
      "capacityProvider": "FARGATE",
      "weight": 1,
      "base": 1
    }
  ]
}
```

---

## Troubleshooting

### Common Issues

1. **Task won't start**: Check CloudWatch logs, verify secrets exist
2. **Health checks failing**: Ensure security groups allow ALB → ECS traffic
3. **High latency**: Check if LLM provider is rate limiting, scale up

### Useful Commands

```bash
# View ECS service events
aws ecs describe-services --cluster devops-copilot --services devops-copilot-service

# View task logs
aws logs tail /ecs/devops-copilot --follow

# Force new deployment
aws ecs update-service --cluster devops-copilot --service devops-copilot-service --force-new-deployment
```

---

## Next Steps

1. Set up CI/CD pipeline (GitHub Actions → ECR → ECS)
2. Configure custom domain with Route 53
3. Enable AWS X-Ray for distributed tracing
4. Set up backup strategy for RDS
5. Configure disaster recovery across regions
