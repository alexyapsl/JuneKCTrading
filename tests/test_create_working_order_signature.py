"""
Unit test to discover the actual signature of IGService.create_working_order.
Run this to see exactly what parameters the trading-ig library accepts.
"""
import inspect
from trading_ig import IGService

def test_create_working_order_signature():
    """Inspect the actual signature of create_working_order."""
    sig = inspect.signature(IGService.create_working_order)
    print("=" * 70)
    print("IGService.create_working_order signature:")
    print("=" * 70)
    print(f"def create_working_order{sig}")
    print()
    print("Parameters:")
    for name, param in sig.parameters.items():
        default = param.default if param.default != inspect.Parameter.empty else "<required>"
        kind = param.kind.name
        print(f"  {name:20s} kind={kind:15s} default={default}")
    print("=" * 70)
    return sig

if __name__ == "__main__":
    test_create_working_order_signature()