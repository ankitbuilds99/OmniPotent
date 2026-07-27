"""Tests for customer and trial management."""



class TestCustomerCreation:
    """Tests for customer creation."""

    def setup_method(self):
        """Clear customers before each test."""
        from app.customers.manager import clear_customers
        clear_customers()

    def test_create_customer(self):
        """Test basic customer creation."""
        from app.customers import create_customer

        customer, message = create_customer(
            email="cust@startup.io",
            company="Startup Inc",
        )

        assert customer is not None
        assert customer.email == "cust@startup.io"
        assert customer.company == "Startup Inc"
        assert customer.id.startswith("cust_")
        assert "success" in message.lower()

    def test_create_customer_with_details(self):
        """Test customer creation with full details."""
        from app.customers import create_customer, CustomerStatus

        customer, _ = create_customer(
            email="detailed@company.com",
            company="Detailed Corp",
            name="John Smith",
            role="VP Engineering",
            team_size="50-100",
            industry="Fintech",
            use_case="Reduce incident response time",
        )

        assert customer.name == "John Smith"
        assert customer.role == "VP Engineering"
        assert customer.team_size == "50-100"
        assert customer.industry == "Fintech"
        assert customer.status == CustomerStatus.TRIAL

    def test_duplicate_email_returns_existing(self):
        """Test that duplicate email returns existing customer."""
        from app.customers import create_customer

        cust1, msg1 = create_customer("dup@example.com", "First Co")
        cust2, msg2 = create_customer("dup@example.com", "Second Co")

        assert cust1.id == cust2.id
        assert "exists" in msg2.lower()

    def test_invalid_email_rejected(self):
        """Test that invalid email is rejected."""
        from app.customers import create_customer

        customer, message = create_customer("invalid", "Test Co")

        assert customer is None
        assert "email" in message.lower()


class TestTrialManagement:
    """Tests for trial management."""

    def setup_method(self):
        """Clear customers before each test."""
        from app.customers.manager import clear_customers
        clear_customers()

    def test_create_trial(self):
        """Test trial creation."""
        from app.customers import create_customer, create_trial, TrialStatus

        customer, _ = create_customer("trial@test.com", "Trial Co")
        trial, message = create_trial(customer.id)

        assert trial is not None
        assert trial.customer_id == customer.id
        assert trial.status == TrialStatus.ACTIVE
        assert trial.days_remaining > 0
        assert trial.investigations_allowed == 10

    def test_trial_custom_duration(self):
        """Test trial with custom duration."""
        from app.customers import create_customer, create_trial

        customer, _ = create_customer("custom@test.com", "Custom Co")
        trial, _ = create_trial(
            customer.id,
            duration_days=30,
            investigations_allowed=20,
            tokens_allowed=200000,
        )

        assert trial.days_remaining <= 30
        assert trial.investigations_allowed == 20
        assert trial.tokens_allowed == 200000

    def test_trial_for_nonexistent_customer(self):
        """Test trial creation for non-existent customer."""
        from app.customers import create_trial

        trial, message = create_trial("cust_nonexistent")

        assert trial is None
        assert "not found" in message.lower()

    def test_active_trial_check(self):
        """Test active trial detection."""
        from app.customers import create_customer, create_trial, get_active_trials

        customer, _ = create_customer("active@test.com", "Active Co")
        create_trial(customer.id)

        active = get_active_trials()

        assert len(active) == 1
        assert active[0].customer_id == customer.id

    def test_trial_usage_tracking(self):
        """Test trial usage percentage calculation."""
        from app.customers import (
            create_customer,
            create_trial,
            record_usage,
            get_trial,
        )

        customer, _ = create_customer("usage@test.com", "Usage Co")
        trial, _ = create_trial(customer.id, investigations_allowed=10)

        record_usage(
            customer_id=customer.id,
            event_type="investigation",
            investigation_id="inv_001",
            tokens_used=5000,
        )

        updated_trial = get_trial(trial.id)

        assert updated_trial.investigations_used == 1
        assert updated_trial.tokens_used == 5000
        assert updated_trial.usage_percent == 10.0


class TestTrialConversion:
    """Tests for trial to paid conversion."""

    def setup_method(self):
        """Clear customers before each test."""
        from app.customers.manager import clear_customers
        clear_customers()

    def test_convert_trial(self):
        """Test converting trial to paid."""
        from app.customers import (
            create_customer,
            create_trial,
            convert_trial,
            get_customer,
            CustomerTier,
            CustomerStatus,
        )

        customer, _ = create_customer("convert@test.com", "Convert Co")
        create_trial(customer.id)

        success, message = convert_trial(
            customer_id=customer.id,
            tier=CustomerTier.PRO,
            mrr_cents=49900,
        )

        assert success

        updated = get_customer(customer_id=customer.id)
        assert updated.status == CustomerStatus.ACTIVE
        assert updated.tier == CustomerTier.PRO
        assert updated.mrr_cents == 49900
        assert updated.is_paying

    def test_convert_nonexistent_customer(self):
        """Test converting non-existent customer."""
        from app.customers import convert_trial

        success, message = convert_trial(customer_id="cust_ghost")

        assert not success
        assert "not found" in message.lower()


class TestUsageTracking:
    """Tests for customer usage tracking."""

    def setup_method(self):
        """Clear customers before each test."""
        from app.customers.manager import clear_customers
        clear_customers()

    def test_record_usage(self):
        """Test recording customer usage."""
        from app.customers import create_customer, record_usage, get_customer

        customer, _ = create_customer("track@test.com", "Track Co")

        success = record_usage(
            customer_id=customer.id,
            event_type="investigation",
            investigation_id="inv_123",
            tokens_used=1500,
            duration_seconds=120.5,
        )

        assert success

        updated = get_customer(customer_id=customer.id)
        assert updated.total_investigations == 1
        assert updated.total_tokens_used == 1500
        assert updated.last_active_at is not None

    def test_record_multiple_usage_events(self):
        """Test recording multiple usage events."""
        from app.customers import create_customer, record_usage, get_customer

        customer, _ = create_customer("multi@test.com", "Multi Co")

        for i in range(5):
            record_usage(
                customer_id=customer.id,
                event_type="investigation",
                investigation_id=f"inv_{i}",
                tokens_used=1000,
            )

        updated = get_customer(customer_id=customer.id)
        assert updated.total_investigations == 5
        assert updated.total_tokens_used == 5000

    def test_record_usage_nonexistent_customer(self):
        """Test recording usage for non-existent customer."""
        from app.customers import record_usage

        success = record_usage(
            customer_id="cust_ghost",
            event_type="investigation",
        )

        assert not success


class TestHealthScore:
    """Tests for customer health score calculation."""

    def setup_method(self):
        """Clear customers before each test."""
        from app.customers.manager import clear_customers
        clear_customers()

    def test_baseline_health_score(self):
        """Test baseline health score for new customer."""
        from app.customers import create_customer

        customer, _ = create_customer("health@test.com", "Health Co")

        assert customer.health_score >= 50

    def test_health_score_increases_with_usage(self):
        """Test health score increases with usage."""
        from app.customers import create_customer, record_usage, get_customer

        customer, _ = create_customer("active@test.com", "Active Co")
        initial_score = customer.health_score

        for i in range(5):
            record_usage(
                customer_id=customer.id,
                event_type="investigation",
                investigation_id=f"inv_{i}",
            )

        updated = get_customer(customer_id=customer.id)
        assert updated.health_score > initial_score


class TestCustomerStats:
    """Tests for customer statistics."""

    def setup_method(self):
        """Clear customers before each test."""
        from app.customers.manager import clear_customers
        clear_customers()

    def test_empty_stats(self):
        """Test stats with no customers."""
        from app.customers import get_customer_stats

        stats = get_customer_stats()

        assert stats["total_customers"] == 0
        assert stats["mrr_usd"] == 0.0

    def test_stats_with_customers(self):
        """Test stats calculation."""
        from app.customers import (
            create_customer,
            create_trial,
            convert_trial,
            get_customer_stats,
            CustomerTier,
        )

        cust1, _ = create_customer("stat1@test.com", "Stats Co 1")
        cust2, _ = create_customer("stat2@test.com", "Stats Co 2")

        create_trial(cust1.id)
        create_trial(cust2.id)

        convert_trial(cust1.id, tier=CustomerTier.STARTER, mrr_cents=9900)

        stats = get_customer_stats()

        assert stats["total_customers"] == 2
        assert stats["total_trials"] == 2
        assert stats["paying_customers"] == 1
        assert stats["mrr_usd"] == 99.0


class TestCustomerManager:
    """Tests for CustomerManager class."""

    def setup_method(self):
        """Clear customers before each test."""
        from app.customers.manager import clear_customers
        clear_customers()

    def test_manager_create_with_trial(self):
        """Test manager create with auto trial."""
        from app.customers import CustomerManager

        manager = CustomerManager()
        result = manager.create(
            email="mgr@test.com",
            company="Manager Co",
            start_trial=True,
        )

        assert result["success"]
        assert "customer" in result
        assert "trial" in result
        assert result["trial"]["days_remaining"] > 0

    def test_manager_create_without_trial(self):
        """Test manager create without trial."""
        from app.customers import CustomerManager

        manager = CustomerManager()
        result = manager.create(
            email="notrial@test.com",
            company="No Trial Co",
            start_trial=False,
        )

        assert result["success"]
        assert "customer" in result
        assert "trial" not in result

    def test_manager_dashboard(self):
        """Test manager dashboard data."""
        from app.customers import CustomerManager, create_customer, create_trial

        customer, _ = create_customer("dash@test.com", "Dashboard Co")
        create_trial(customer.id)

        manager = CustomerManager()
        dashboard = manager.get_dashboard()

        assert "stats" in dashboard
        assert "active_trials" in dashboard
        assert "recent_customers" in dashboard
        assert len(dashboard["active_trials"]) == 1
