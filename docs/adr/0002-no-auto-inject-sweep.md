# No auto-inject sweep over `WorkerSettings.functions`

`@inject` is applied per task at the definition site: `setup_di` wraps the four lifecycle hooks and
never walks `WorkerSettings.functions` wrapping what it finds. A sweep is the obvious convenience,
and a one-liner in integrations whose handler registry is homogeneous, but arq's is not. An entry is
a plain coroutine, a `Function` record from `arq.func(...)` carrying its own name, timeout, result
retention and retry settings, or an import string `arq.func` resolves at worker construction, so a
sweep must recognise each shape, reach the coroutine inside, and rebuild the record without dropping
any of its six fields, against a pre-1.0 library, with every path failing by silently not injecting
rather than by raising. `@inject` costs one line, fails loudly at decoration, and is visible in the
task's own source; it also returns a task with no `FromDI` parameter unchanged, so a sweep would
save a line only where dependencies are declared.
