# Examples

Small, runnable scripts demonstrating the Baton library API (see the main
[README](../README.md#usage) for the full CLI / Web UI / library overview).

- **`fake_provider.py`** — fully offline, no API key needed. Wires
  `baton.providers.fake.FakeProvider` into a `Runtime` by hand and runs one goal
  end-to-end (plan -> parallel workers -> synthesis). Run: `uv run python examples/fake_provider.py`
- **`minimal_library.py`** — builds a runtime from your environment
  (`baton.build_providers_from_env` + `baton.make_runtime_factory`) and runs a real
  goal against whatever provider you have configured. Run:
  `uv run python examples/minimal_library.py "your goal"`
