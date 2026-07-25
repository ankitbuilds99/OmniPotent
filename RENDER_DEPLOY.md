# Deploy DevOps Copilot to Render

## 🚀 One-Click Deploy

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ankit25bcs10610/DevOps-Copilot)

---

## 📋 Manual Deploy Steps

### Step 1: Fork/Push to GitHub

Make sure your repo is on GitHub:
```bash
git add .
git commit -m "Add Render deployment configuration"
git push origin main
```

### Step 2: Create Render Account

1. Go to [render.com](https://render.com)
2. Sign up with GitHub

### Step 3: Deploy via Blueprint

1. Click **New** → **Blueprint**
2. Connect your GitHub repo: `ankit25bcs10610/DevOps-Copilot`
3. Render will auto-detect `render.yaml`
4. Click **Apply**

### Step 4: Add API Key (Required!)

After deployment:

1. Go to your service **Dashboard**
2. Click **Environment**
3. Add your LLM API key:
   ```
   ANTHROPIC_API_KEY = sk-ant-your-key-here
   ```
4. Click **Save Changes**
5. Service will auto-redeploy

---

## 🔧 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ Yes | Your Claude API key |
| `COPILOT_PROVIDER` | No | `anthropic` (default), `openai`, `gemini`, `groq` |
| `OPENAI_API_KEY` | If using OpenAI | Your OpenAI key |
| `COPILOT_API_TOKEN` | Auto-generated | API auth token |

---

## 🌐 Access Your App

After deploy completes:

```
https://devops-copilot.onrender.com
```

(Your URL will be shown in Render dashboard)

---

## 💰 Pricing

| Resource | Plan | Cost |
|----------|------|------|
| Web Service | Free | $0/month |
| PostgreSQL | Free | $0/month |

**Note:** Free tier services sleep after 15 minutes of inactivity. First request after sleep takes ~30 seconds to wake up.

For always-on: Upgrade to Starter ($7/month)

---

## 🔍 Troubleshooting

### Service won't start
- Check **Logs** tab in Render dashboard
- Verify `ANTHROPIC_API_KEY` is set

### 502 Bad Gateway
- Service is still starting (wait 2-3 minutes)
- Or service crashed - check logs

### "API key not set" error
- Go to Environment → Add `ANTHROPIC_API_KEY`

---

## 📊 Monitoring

View real-time logs:
```
Render Dashboard → Your Service → Logs
```

---

## 🔄 Auto-Deploy

Every push to `main` branch automatically deploys!

To disable:
1. Go to Settings
2. Turn off "Auto-Deploy"
