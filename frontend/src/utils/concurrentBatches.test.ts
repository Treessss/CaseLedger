import { runConcurrentBatches } from './concurrentBatches';

test('limits parallel batches and retains results in input order', async () => {
  let active = 0;
  let maximum = 0;
  const releases: Array<() => void> = [];
  const progress: number[] = [];
  const promise = runConcurrentBatches([0, 1, 2, 3], 2, async (value) => {
    active++;
    maximum = Math.max(maximum, active);
    await new Promise<void>((resolve) => releases.push(resolve));
    active--;
    return value * 10;
  }, (completed) => progress.push(completed));

  expect(releases).toHaveLength(2);
  releases[1]();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(releases).toHaveLength(3);
  releases[0]();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(releases).toHaveLength(4);
  releases[2]();
  releases[3]();

  expect(await promise).toEqual([0, 10, 20, 30].map((value) => ({ status: 'fulfilled', value })));
  expect(maximum).toBe(2);
  expect(progress).toEqual([1, 2, 3, 4]);
});

test('one failed batch does not prevent other batches from finishing', async () => {
  const processed: number[] = [];
  const outcomes = await runConcurrentBatches([1, 2, 3], 2, async (value) => {
    processed.push(value);
    if (value === 2) throw new Error('uncertain writeback');
    return value;
  });
  expect(processed).toEqual([1, 2, 3]);
  expect(outcomes.map((row) => row.status)).toEqual(['fulfilled', 'rejected', 'fulfilled']);
});
