/** Run independent batches with a strict concurrency cap and retain every outcome. */
export async function runConcurrentBatches<T, R>(
  batches: T[],
  concurrency: number,
  processBatch: (batch: T, index: number) => Promise<R>,
  onProgress?: (completed: number, outcomes: Array<PromiseSettledResult<R> | undefined>) => void,
): Promise<Array<PromiseSettledResult<R>>> {
  const outcomes: Array<PromiseSettledResult<R> | undefined> = Array(batches.length);
  let nextIndex = 0;
  let completed = 0;

  const worker = async () => {
    while (nextIndex < batches.length) {
      const index = nextIndex++;
      try {
        outcomes[index] = { status: 'fulfilled', value: await processBatch(batches[index], index) };
      } catch (reason) {
        outcomes[index] = { status: 'rejected', reason };
      }
      completed++;
      onProgress?.(completed, outcomes);
    }
  };

  await Promise.all(Array.from({ length: Math.min(Math.max(1, concurrency), batches.length) }, worker));
  return outcomes as Array<PromiseSettledResult<R>>;
}
