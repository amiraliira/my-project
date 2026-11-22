import base64
import hashlib
import threading
import unittest
from decimal import Decimal

from pak_maintenance.common import ApiError, User, dumps, loads
from pak_maintenance.security import (LoginGuard, hash_password, require,
                                      token_digest, verify_login, verify_password)


class PasswordCompatibilityTests(unittest.TestCase):
    def test_existing_dotnet_hash_formats_remain_valid(self):
        password = "رمز آزمایشی-Pak-123"
        salt = bytes(range(16))
        for count in (210_000, 600_000):
            with self.subTest(iterations=count):
                expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, count, dklen=32)
                encoded = f"pbkdf2-sha256${count}$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(expected).decode()
                self.assertTrue(verify_password(password, encoded))
                self.assertTrue(verify_login(password, encoded))
                self.assertFalse(verify_password(password + "x", encoded))

    def test_new_passwords_are_salted_and_no_plaintext_is_stored(self):
        password = "DifferentPassword123!"
        first, second = hash_password(password), hash_password(password)
        self.assertNotEqual(first, second)
        self.assertNotIn(password, first)
        self.assertTrue(verify_password(password, first))

    def test_malformed_hashes_do_not_allow_parameter_exhaustion(self):
        for value in ("", "x" * 10000, "pbkdf2-sha256$999999999999$AAAA$AAAA",
                      "pbkdf2-sha256$600000$!!!$!!!", "pbkdf2-sha256$600000$AAAA$AAAA"):
            with self.subTest(value=value[:30]):
                self.assertFalse(verify_password("validPassword123", value))

    def test_missing_account_and_unusable_hash_fail_login(self):
        self.assertFalse(verify_login("validPassword123", None))
        self.assertFalse(verify_login("validPassword123", "unusable"))

    def test_session_digest_compatible_with_dotnet_uppercase_utf8(self):
        token = "A1" * 32
        self.assertEqual(token_digest(token), hashlib.sha256(token.encode("utf-8")).hexdigest().upper())


class AccessTests(unittest.TestCase):
    def test_unknown_disabled_and_unauthenticated_accounts_fail_closed(self):
        for user in (None, User("x", role="owner"), User("x", role="admin", active=False)):
            with self.subTest(user=user):
                with self.assertRaises(ApiError) as error:
                    require(user, registration=True)
                self.assertEqual(error.exception.status, 401)

    def test_technician_is_denied_on_new_unmarked_api(self):
        user = User("t", role="technician", specialty="مکانیک")
        with self.assertRaises(ApiError) as error:
            require(user)
        self.assertEqual(error.exception.status, 403)
        self.assertIs(require(user, registration=True), user)
        with self.assertRaises(ApiError):
            require(user, "admin", registration=True)

    def test_it_admin_is_not_a_professional_approver(self):
        admin = User("a", role="admin")
        self.assertIs(require(admin, "admin"), admin)
        with self.assertRaises(ApiError):
            require(admin, "head", "manager")


class LoginBudgetTests(unittest.TestCase):
    def test_account_budget_survives_ip_rotation(self):
        guard = LoginGuard(max_account=2)
        for ip in ("10.0.0.1", "10.0.0.2"):
            with guard.check(ip, "same-account"):
                pass
        with self.assertRaises(ApiError) as error:
            with guard.check("10.0.0.3", "same-account"):
                pass
        self.assertEqual(error.exception.status, 429)

    def test_ip_budget_survives_account_rotation(self):
        guard = LoginGuard(max_address=2)
        for account in ("one", "two"):
            with guard.check("10.0.0.1", account):
                pass
        with self.assertRaises(ApiError):
            with guard.check("10.0.0.1", "three"):
                pass

    def test_expired_budgets_recover_and_memory_is_bounded(self):
        clock = [100.0]
        guard = LoginGuard(window=10, max_keys=1, clock=lambda: clock[0])
        with guard.check("10.0.0.1", "first"):
            pass
        with self.assertRaises(ApiError):
            with guard.check("10.0.0.2", "second"):
                pass
        clock[0] += 11
        with guard.check("10.0.0.2", "second"):
            pass
        self.assertEqual(len(guard._accounts), 1)
        self.assertEqual(len(guard._addresses), 1)

    def test_parallel_password_work_releases_slots_after_failure(self):
        guard = LoginGuard(parallel_checks=1, wait_seconds=0.01)
        outcome = []
        def second():
            try:
                with guard.check("10.0.0.2", "other"):
                    outcome.append("unexpected")
            except ApiError as error:
                outcome.append(error.status)
        with self.assertRaises(RuntimeError):
            with guard.check("10.0.0.1", "one"):
                thread = threading.Thread(target=second)
                thread.start()
                thread.join(1)
                self.assertFalse(thread.is_alive())
                raise RuntimeError("failed verification")
        self.assertEqual(outcome, [429])
        with guard.check("10.0.0.3", "third"):
            pass
        self.assertEqual(guard._pending, 0)


class JsonBoundaryTests(unittest.TestCase):
    def test_decimal_values_round_trip_without_binary_float_loss(self):
        value = {"quantity": Decimal("999999999.123456"), "score": Decimal("0.005")}
        self.assertEqual(loads(dumps(value)), value)

    def test_nonfinite_and_duplicate_numbers_fail_closed(self):
        for document in ('{"coefficient":NaN}', '{"a":1,"a":2}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                loads(document)
        with self.assertRaises(ValueError):
            dumps({"value": Decimal("NaN")})


if __name__ == "__main__":
    unittest.main()
