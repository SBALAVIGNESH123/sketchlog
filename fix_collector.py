c = open('python/sketchlog/ebpf/collector.py').read()
c = c.replace('alpha = self.log._relative_accuracy', 'alpha = self.log.relative_accuracy')
c = c.replace('gamma = (1.0 + self.log._relative_accuracy) / (1.0 - self.log._relative_accuracy)', 'gamma = (1.0 + self.log.relative_accuracy) / (1.0 - self.log.relative_accuracy)')
c = c.replace('self._thread.join()', 'self._thread.join(timeout=5.0)\n            if self._thread.is_alive():\n                print("Warning: EBPFCollector polling thread did not exit cleanly", flush=True)')
c = c.replace('self.log.add_batch([bound_val] * total_count)', '''batch_size = min(total_count, 1000)
                    batch = [bound_val] * batch_size
                    for _ in range(total_count // batch_size):
                        self.log.add_batch(batch)
                    if total_count % batch_size:
                        self.log.add_batch([bound_val] * (total_count % batch_size))''')
c = c.replace('bucket_counts[ctypes.c_int(i)] = bucket_counts.leaf()', 'bucket_counts[ctypes.c_int(i)] = bucket_counts.Leaf()')
open('python/sketchlog/ebpf/collector.py', 'w').write(c)
