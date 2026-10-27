import copy
import unittest
from tools import config_generator as config


class ConfigGeneratorTests(unittest.TestCase):
    def test_development_overrides_keep_unrelated_defaults(self):
        value = config.generate_config("development")
        self.assertEqual(value["app"]["environment"], "development")
        self.assertTrue(value["app"]["debug"])
        self.assertEqual(value["database"]["name"], "tent_dev")
        self.assertEqual(value["market"]["rate_limit_per_second"], 1000)
        self.assertEqual(value["auth"]["jwt_expiry_minutes"], 1440)
        self.assertEqual(value["server"], config.DEFAULT_CONFIG["server"])
        self.assertEqual(value["market"]["fees"], config.DEFAULT_CONFIG["market"]["fees"])

    def test_production_security_and_pool_overrides(self):
        value = config.generate_config("production")
        self.assertEqual(value["app"]["environment"], "production")
        self.assertFalse(value["app"]["debug"])
        self.assertEqual(value["database"]["name"], "tent_production")
        self.assertEqual(value["database"]["pool_min"], 10)
        self.assertEqual(value["database"]["pool_max"], 50)
        self.assertTrue(value["auth"]["mfa_required"])
        self.assertTrue(value["features"]["margin_trading"])
        self.assertEqual(value["monitoring"]["tracing_sample_rate"], 0.01)
        self.assertEqual(value["server"]["port"], 8080)

    def test_custom_overrides_win_after_environment_without_mutating_inputs(self):
        defaults = copy.deepcopy(config.DEFAULT_CONFIG)
        environments = copy.deepcopy(config.ENV_OVERRIDES)
        override = {"database": {"pool_max": 75}, "features": {"dark_mode": False}}
        before = copy.deepcopy(override)
        value = config.generate_config("production", override)
        self.assertEqual(value["database"]["pool_max"], 75)
        self.assertEqual(value["database"]["pool_min"], 10)
        self.assertFalse(value["features"]["dark_mode"])
        self.assertTrue(value["features"]["margin_trading"])
        self.assertEqual(override, before)
        self.assertEqual(config.DEFAULT_CONFIG, defaults)
        self.assertEqual(config.ENV_OVERRIDES, environments)

    def test_environment_generation_order_does_not_contaminate_defaults(self):
        first = config.generate_config("development")
        config.generate_config("production")
        self.assertEqual(config.generate_config("development"), first)
        self.assertEqual(config.generate_config("unknown"), config.DEFAULT_CONFIG)

    def test_recursive_merge_preserves_siblings_and_both_arguments(self):
        base = {"service": {"limits": {"read": 3, "write": 5}, "enabled": True}, "other": {"port": 80}}
        override = {"service": {"limits": {"read": 9}}, "new": "value"}
        before_base, before_override = copy.deepcopy(base), copy.deepcopy(override)
        merged = config.merge_config(base, override)
        self.assertEqual(merged, {"service": {"limits": {"read": 9, "write": 5}, "enabled": True}, "other": {"port": 80}, "new": "value"})
        self.assertEqual(base, before_base)
        self.assertEqual(override, before_override)

    def test_merge_replaces_lists_and_scalar_dictionary_types(self):
        self.assertEqual(config.merge_config({"a": [1, 2], "b": {"x": 1}, "c": 3}, {"a": [4], "b": None, "c": {"nested": True}}), {"a": [4], "b": None, "c": {"nested": True}})
        self.assertEqual(config.merge_config({}, {"flag": False}), {"flag": False})

    def test_mask_redacts_all_sensitive_values_and_preserves_input(self):
        value = config.generate_config("production", {"database": {"password": "synthetic-db"}, "redis": {"password": "synthetic-cache"}, "auth": {"jwt_secret": "synthetic-jwt"}})
        before = copy.deepcopy(value)
        masked = config.mask_sensitive(value)
        self.assertEqual(masked["database"]["password"], "***REDACTED***")
        self.assertEqual(masked["redis"]["password"], "***REDACTED***")
        self.assertEqual(masked["auth"]["jwt_secret"], "***REDACTED***")
        self.assertEqual(masked["database"]["name"], "tent_production")
        self.assertEqual(masked["auth"]["jwt_expiry_minutes"], 60)
        self.assertEqual(masked["features"], value["features"])
        self.assertEqual(value, before)
        self.assertNotIn("synthetic-", config.to_json(masked))

    def test_mask_uses_full_paths_and_handles_empty_values(self):
        self.assertEqual(config.mask_sensitive({"password": "visible", "auth": {"jwt_secret": ""}, "other": {"jwt_secret": "visible"}}), {"password": "visible", "auth": {"jwt_secret": "***REDACTED***"}, "other": {"jwt_secret": "visible"}})
        self.assertEqual(config.mask_sensitive({"jwt_secret": None}, "auth"), {"jwt_secret": "***REDACTED***"})
        self.assertEqual(config.mask_sensitive({}), {})

    def test_sensitive_paths_are_unique_and_complete(self):
        self.assertCountEqual(config.SENSITIVE_KEYS, ["database.password", "redis.password", "auth.jwt_secret"])


if __name__ == "__main__":
    unittest.main()
