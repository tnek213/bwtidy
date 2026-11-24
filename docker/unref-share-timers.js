// Preloaded into every bw of the image (Dockerfile target app, so the dev image too).
//
// bw 2026.10.0 shares the observables of its state providers with
// share({ resetOnRefCountZero: () => timer(cleanupDelayMs) }). When a command is done, those
// timers (rxjs AsyncAction, a setInterval of 1000 ms) keep Node's event loop alive about 2 s
// longer, two of them in a chain. They only drop a cache nobody subscribes to any more, so
// this calls unref() on them and the process exits once its work is done. Every other timer
// stays as it is: bw awaits real ones (retries, the lock file), an unref of those could end
// a command midway.
'use strict'

const setInterval = globalThis.setInterval
// Node keeps 10 frames by default, share's handleReset is the 11th below this function
const FRAMES = 20
const SHARE_RESET = /\bat handleReset \(\S*\/rxjs\/\S*\/operators\/share\.js:/

globalThis.setInterval = function (callback, delay, ...args) {
  const handle = setInterval.call(this, callback, delay, ...args)
  const limit = Error.stackTraceLimit
  Error.stackTraceLimit = FRAMES
  const stack = new Error().stack
  Error.stackTraceLimit = limit
  if (SHARE_RESET.test(stack)) {
    handle.unref()
  }
  return handle
}
