"""Tests for waitlist management."""



class TestWaitlistSignup:
    """Tests for waitlist signup functionality."""

    def setup_method(self):
        """Clear waitlist before each test."""
        from app.waitlist.manager import clear_waitlist
        clear_waitlist()

    def test_basic_signup(self):
        """Test basic email signup."""
        from app.waitlist import add_to_waitlist, SignupSource

        entry, message = add_to_waitlist(
            email="test@example.com",
            source=SignupSource.LANDING_PAGE,
        )

        assert entry is not None
        assert entry.email == "test@example.com"
        assert entry.position == 1
        assert entry.referral_code.startswith("REF")
        assert "waitlist" in message.lower()

    def test_signup_with_company_info(self):
        """Test signup with company information."""
        from app.waitlist import add_to_waitlist, SignupSource

        entry, _ = add_to_waitlist(
            email="cto@startup.io",
            source=SignupSource.PRODUCT_HUNT,
            company="Startup Inc",
            role="CTO",
            team_size="10-50",
            use_case="Reduce MTTR by 50%",
        )

        assert entry is not None
        assert entry.company == "Startup Inc"
        assert entry.role == "CTO"
        assert entry.team_size == "10-50"
        assert entry.source == SignupSource.PRODUCT_HUNT

    def test_duplicate_email_returns_existing(self):
        """Test that duplicate email returns existing entry."""
        from app.waitlist import add_to_waitlist

        entry1, msg1 = add_to_waitlist(email="dup@example.com")
        entry2, msg2 = add_to_waitlist(email="dup@example.com")

        assert entry1 is not None
        assert entry2 is not None
        assert entry1.id == entry2.id
        assert "already" in msg2.lower()

    def test_invalid_email_rejected(self):
        """Test that invalid emails are rejected."""
        from app.waitlist import add_to_waitlist

        entry, message = add_to_waitlist(email="not-an-email")

        assert entry is None
        assert "invalid" in message.lower()

    def test_empty_email_rejected(self):
        """Test that empty email is rejected."""
        from app.waitlist import add_to_waitlist

        entry, message = add_to_waitlist(email="")

        assert entry is None
        assert "required" in message.lower()

    def test_position_increments(self):
        """Test that positions increment correctly."""
        from app.waitlist import add_to_waitlist

        entry1, _ = add_to_waitlist(email="first@example.com")
        entry2, _ = add_to_waitlist(email="second@example.com")
        entry3, _ = add_to_waitlist(email="third@example.com")

        assert entry1.position == 1
        assert entry2.position == 2
        assert entry3.position == 3

    def test_referral_tracking(self):
        """Test referral code tracking."""
        from app.waitlist import add_to_waitlist, get_waitlist_entry

        referrer, _ = add_to_waitlist(email="referrer@example.com")
        referred, _ = add_to_waitlist(
            email="referred@example.com",
            referred_by=referrer.email,
        )

        assert referred.referred_by == referrer.email

        updated_referrer = get_waitlist_entry(referrer.email)
        assert updated_referrer.referral_count == 1

    def test_referral_upgrade_after_3(self):
        """Test tier upgrade after 3 referrals."""
        from app.waitlist import add_to_waitlist, get_waitlist_entry, WaitlistTier

        referrer, _ = add_to_waitlist(email="popular@example.com")

        for i in range(3):
            add_to_waitlist(
                email=f"friend{i}@example.com",
                referred_by=referrer.email,
            )

        updated = get_waitlist_entry(referrer.email)
        assert updated.tier == WaitlistTier.PRIORITY
        assert updated.referral_count == 3


class TestWaitlistStats:
    """Tests for waitlist statistics."""

    def setup_method(self):
        """Clear waitlist before each test."""
        from app.waitlist.manager import clear_waitlist
        clear_waitlist()

    def test_empty_stats(self):
        """Test stats with empty waitlist."""
        from app.waitlist import get_waitlist_stats

        stats = get_waitlist_stats()

        assert stats["total_signups"] == 0
        assert stats["conversion_rate"] == 0.0

    def test_stats_by_source(self):
        """Test stats breakdown by source."""
        from app.waitlist import add_to_waitlist, get_waitlist_stats, SignupSource

        add_to_waitlist("a@example.com", source=SignupSource.LANDING_PAGE)
        add_to_waitlist("b@example.com", source=SignupSource.LANDING_PAGE)
        add_to_waitlist("c@example.com", source=SignupSource.PRODUCT_HUNT)

        stats = get_waitlist_stats()

        assert stats["total_signups"] == 3
        assert stats["by_source"]["landing_page"] == 2
        assert stats["by_source"]["product_hunt"] == 1


class TestWaitlistPromotion:
    """Tests for waitlist tier promotion."""

    def setup_method(self):
        """Clear waitlist before each test."""
        from app.waitlist.manager import clear_waitlist
        clear_waitlist()

    def test_promote_to_early_access(self):
        """Test promoting user to early access."""
        from app.waitlist import (
            add_to_waitlist,
            promote_to_early_access,
            get_waitlist_entry,
            WaitlistTier,
        )

        add_to_waitlist(email="vip@example.com")

        success, message = promote_to_early_access("vip@example.com")

        assert success
        entry = get_waitlist_entry("vip@example.com")
        assert entry.tier == WaitlistTier.EARLY_ACCESS
        assert entry.invited_at is not None

    def test_promote_nonexistent_email(self):
        """Test promoting non-existent email."""
        from app.waitlist import promote_to_early_access

        success, message = promote_to_early_access("ghost@example.com")

        assert not success
        assert "not found" in message.lower()


class TestWaitlistManager:
    """Tests for WaitlistManager class."""

    def setup_method(self):
        """Clear waitlist before each test."""
        from app.waitlist.manager import clear_waitlist
        clear_waitlist()

    def test_manager_signup(self):
        """Test manager signup method."""
        from app.waitlist import WaitlistManager

        manager = WaitlistManager()
        result = manager.signup(email="manager@example.com", company="Test Co")

        assert result["success"]
        assert result["position"] == 1
        assert "referral_code" in result

    def test_manager_get_position(self):
        """Test manager position lookup."""
        from app.waitlist import WaitlistManager

        manager = WaitlistManager()
        manager.signup(email="lookup@example.com")

        result = manager.get_position("lookup@example.com")

        assert result["found"]
        assert result["position"] == 1

    def test_manager_position_not_found(self):
        """Test position lookup for non-existent email."""
        from app.waitlist import WaitlistManager

        manager = WaitlistManager()
        result = manager.get_position("unknown@example.com")

        assert not result["found"]


class TestWaitlistExport:
    """Tests for waitlist export functionality."""

    def setup_method(self):
        """Clear waitlist before each test."""
        from app.waitlist.manager import clear_waitlist
        clear_waitlist()

    def test_export_csv(self):
        """Test CSV export."""
        from app.waitlist import add_to_waitlist, export_waitlist

        add_to_waitlist("export1@example.com", company="Company A")
        add_to_waitlist("export2@example.com", company="Company B")

        csv_data = export_waitlist(format="csv")

        assert "position" in csv_data
        assert "email" in csv_data
        assert "Company A" in csv_data
        assert "Company B" in csv_data

    def test_export_json(self):
        """Test JSON export."""
        import json
        from app.waitlist import add_to_waitlist, export_waitlist

        add_to_waitlist("json@example.com", company="JSON Corp")

        json_data = export_waitlist(format="json")
        parsed = json.loads(json_data)

        assert len(parsed) == 1
        assert parsed[0]["company"] == "JSON Corp"
