# Testing JuneKCTrading

## Running Tests

From the project root:

```bash
# All tests
python -m unittest discover -s tests -v

# Specific module
python -m unittest tests.test_signal_detector -v
```

## Windows Python PATH Note

If you see "Python was not found" when running `python`, do one of:

1. Use `py -m unittest ...` (Windows launcher)
2. Use full path: `C:\Users\alexy\AppData\Local\Programs\Python\Python312\python.exe -m unittest ...`
3. Add Python to PATH via Windows Settings → Apps → Advanced app settings → App execution aliases (disable the "python" alias that points to the Store)

## Test Coverage (Phase 1)

- `test_signal_detector.py` — validates LONG/SHORT signal generation, no-false-positive cases, reset behavior, and Signal dataclass shape.

Future: add tests for `order_manager.py`, `keltner.py`, `ig_rest_client.py` (with mocks).
