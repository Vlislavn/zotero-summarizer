import { transferableAbortController } from 'node:util';

// Keep Request/fetch, controllers and timeout/any signals in the same Node realm.
const controller = transferableAbortController();
globalThis.AbortController = controller.constructor;
globalThis.AbortSignal = controller.signal.constructor;
