# No auto-inject sweep over `WorkerSettings.functions`

**Decision:** `@inject` is applied per task, explicitly. `setup_di` does not walk
`WorkerSettings.functions` wrapping what it finds.

Wrapping every registered task at `setup_di` time is the obvious convenience, and it is what makes
`setup_di` a one-liner in integrations whose handler registry is homogeneous. arq's is not.
`functions` is a heterogeneous list by design: a plain coroutine, an `arq.func(...)` wrapper
carrying its own name/timeout/retry configuration, or an import string arq resolves later. A sweep
has to recognise each shape, unwrap it, wrap the coroutine inside, and rebuild the surrounding
object without dropping the configuration it carried — for an import string, without importing
anything, since arq's own resolution of it is what decides which module is loaded and when.

That is three format-specific code paths against a pre-1.0 library, each of which fails by
silently not injecting rather than by raising, and each of which has to be revisited whenever arq
adds a fourth accepted shape. `@inject` at the definition site costs one line, fails loudly at
decoration when a task's signature cannot support it, and is visible in the task's own source to
anyone reading it.

The convenience is also smaller here than it looks: `inject` already returns a task with no
`FromDI` parameter unchanged, so the sweep would be saving a decorator line only on the tasks that
actually take dependencies.

**Revisit trigger:** arq narrows `functions` to a single shape, or gives its entries a documented
accessor for the underlying coroutine that survives a rewrite — at which point one code path, not
three, is enough, and the sweep can be added without a per-shape guess.
