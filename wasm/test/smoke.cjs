const assert = require('node:assert/strict');
const path = require('node:path');
const { StreamLog } = require('../sketchlog.js');

(async () => {
  await StreamLog.init({
    locateFile: (file) => path.join(__dirname, '..', 'dist', file),
  });
  const log = new StreamLog();
  log.addBatch([10, 20, 30]);
  log.addEvent('ok', 2);
  log.addUnique('alice');
  assert.equal(log.totalEvents, 5n);
  assert.ok(log.p99 > 29 && log.p99 < 31);
  const payload = log.serialize();
  assert.equal(payload.state.total, '5');
  assert.equal(typeof payload.state.events.table[0][0], 'string');
  log.destroy();
  console.log('WASM smoke test passed');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
