"""Master test runner for HGT-QF Data Desk test suite."""
import sys
import unittest

if __name__ == "__main__":
    # Ensure stdout handles unicode if possible, or fallback safely
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total_tests = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)

    print("\n" + "=" * 60)
    print("HGT-QF Data Desk Test Suite Results:")
    print(f"  Total tests run: {total_tests}")
    print(f"  Failures:        {failures}")
    print(f"  Errors:          {errors}")
    print(f"  Skipped:         {skipped}")
    print("=" * 60)

    if result.wasSuccessful():
        print("[SUCCESS] ALL TESTS PASSED SUCCESSFULLY!\n")
        sys.exit(0)
    else:
        print("[FAIL] SOME TESTS FAILED.\n")
        sys.exit(1)

