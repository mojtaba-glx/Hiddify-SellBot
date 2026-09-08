import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Shared.env_utils import env_int, env_float


class EnvIntTests(unittest.TestCase):
    def test_missing_variable_returns_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_int("TEST_MISSING_INT", 42), 42)

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {"TEST_EMPTY_INT": ""}):
            self.assertEqual(env_int("TEST_EMPTY_INT", 7), 7)

    def test_blank_whitespace_returns_default(self):
        with patch.dict(os.environ, {"TEST_BLANK_INT": "   "}):
            self.assertEqual(env_int("TEST_BLANK_INT", 5), 5)

    def test_valid_integer_is_parsed(self):
        with patch.dict(os.environ, {"TEST_VALID_INT": "13"}):
            self.assertEqual(env_int("TEST_VALID_INT", 1), 13)

    def test_negative_integer_is_parsed(self):
        with patch.dict(os.environ, {"TEST_NEG_INT": "-20"}):
            self.assertEqual(env_int("TEST_NEG_INT", 0), -20)

    def test_invalid_value_returns_default(self):
        with patch.dict(os.environ, {"TEST_BAD_INT": "abc"}):
            self.assertEqual(env_int("TEST_BAD_INT", 9), 9)

    def test_float_looking_value_is_invalid_for_int(self):
        with patch.dict(os.environ, {"TEST_FRACTIONAL_INT": "3.7"}):
            self.assertEqual(env_int("TEST_FRACTIONAL_INT", 3), 3)

    def test_value_below_minimum_clamps_to_minimum(self):
        with patch.dict(os.environ, {"TEST_LOW_INT": "-5"}):
            self.assertEqual(env_int("TEST_LOW_INT", 4, minimum=1), 1)

    def test_value_above_maximum_clamps_to_maximum(self):
        with patch.dict(os.environ, {"TEST_HIGH_INT": "99999"}):
            self.assertEqual(env_int("TEST_HIGH_INT", 100, minimum=1, maximum=65535), 65535)

    def test_out_of_range_default_is_clamped(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_int("TEST_DEFAULT_LOW", 0, minimum=1), 1)
            self.assertEqual(env_int("TEST_DEFAULT_HIGH", 90000, minimum=1, maximum=65535), 65535)

    def test_environ_is_not_mutated(self):
        with patch.dict(os.environ, {}, clear=True):
            env_int("TEST_NO_MUTATION", 77, minimum=1)
            self.assertNotIn("TEST_NO_MUTATION", os.environ)

    def test_invalid_value_logs_warning_with_name_only(self):
        with patch.dict(os.environ, {"TEST_WARN_INT": "not-a-number"}):
            with self.assertLogs("Shared.env_utils", level="WARNING") as captured:
                env_int("TEST_WARN_INT", 3)
            joined = "\n".join(captured.output)
            self.assertIn("TEST_WARN_INT", joined)
            self.assertNotIn("not-a-number", joined)


class EnvFloatTests(unittest.TestCase):
    def test_missing_variable_returns_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_float("TEST_MISSING_FLOAT", 1.5), 1.5)

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {"TEST_EMPTY_FLOAT": ""}):
            self.assertEqual(env_float("TEST_EMPTY_FLOAT", 2.5), 2.5)

    def test_valid_float_is_parsed(self):
        with patch.dict(os.environ, {"TEST_VALID_FLOAT": "0.75"}):
            self.assertEqual(env_float("TEST_VALID_FLOAT", 1.0), 0.75)

    def test_integer_string_is_parsed_as_float(self):
        with patch.dict(os.environ, {"TEST_INTLIKE_FLOAT": "8"}):
            self.assertEqual(env_float("TEST_INTLIKE_FLOAT", 1.0), 8.0)

    def test_invalid_value_returns_default(self):
        with patch.dict(os.environ, {"TEST_BAD_FLOAT": "test"}):
            self.assertEqual(env_float("TEST_BAD_FLOAT", 3.0), 3.0)

    def test_nan_returns_default(self):
        with patch.dict(os.environ, {"TEST_NAN_FLOAT": "nan"}):
            self.assertEqual(env_float("TEST_NAN_FLOAT", 4.0), 4.0)

    def test_positive_infinity_returns_default(self):
        with patch.dict(os.environ, {"TEST_INF_FLOAT": "inf"}):
            self.assertEqual(env_float("TEST_INF_FLOAT", 4.0), 4.0)

    def test_negative_infinity_returns_default(self):
        with patch.dict(os.environ, {"TEST_NINF_FLOAT": "-Infinity"}):
            self.assertEqual(env_float("TEST_NINF_FLOAT", 4.0), 4.0)

    def test_value_below_minimum_clamps_to_minimum(self):
        with patch.dict(os.environ, {"TEST_LOW_FLOAT": "-2"}):
            self.assertEqual(env_float("TEST_LOW_FLOAT", 1.0, minimum=0.2), 0.2)

    def test_value_above_maximum_clamps_to_maximum(self):
        with patch.dict(os.environ, {"TEST_HIGH_FLOAT": "500"}):
            self.assertEqual(env_float("TEST_HIGH_FLOAT", 2.0, minimum=0.2, maximum=60.0), 60.0)

    def test_out_of_range_default_is_clamped(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_float("TEST_DEFAULT_LOW_FLOAT", -5.0, minimum=0.0), 0.0)
            self.assertEqual(env_float("TEST_DEFAULT_HIGH_FLOAT", 100.0, minimum=0.2, maximum=10.0), 10.0)

    def test_environ_is_not_mutated(self):
        with patch.dict(os.environ, {}, clear=True):
            env_float("TEST_NO_MUTATION_FLOAT", 0.5, minimum=0.0)
            self.assertNotIn("TEST_NO_MUTATION_FLOAT", os.environ)


if __name__ == "__main__":
    unittest.main()
