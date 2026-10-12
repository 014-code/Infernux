# Current MPM-style JIT fixture

Copy `Assets/Scripts/MpmJitDemo.py` into an Infernux project and attach
`MpmJitDemo` to an empty GameObject before entering Play mode.

The fixture keeps the snow, fluid, and viscoelastic material laws in separate
`@inx.jit.compile(parallel_policy="required")` functions.  Warmup is checked
for zero mutation, and each first step must publish a parallel specialization.
The sample is intentionally CPU-only; GPU compute and rigidbody exchange have
their own native acceptance gates.
