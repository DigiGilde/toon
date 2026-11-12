import { afterEach, describe, expect, it, vi } from 'vitest';

import { throttle } from './timing';

afterEach(() => {
  vi.useRealTimers();
});

describe('throttle', () => {
  it('runs at once, then at most once per wait, and always runs the last request', () => {
    vi.useFakeTimers();
    const run = vi.fn();
    const throttled = throttle(run, 600);

    throttled.call();
    expect(run).toHaveBeenCalledTimes(1);
    throttled.call();
    throttled.call();
    expect(run).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(600);
    expect(run).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(600);
    expect(run).toHaveBeenCalledTimes(2);

    throttled.call();
    expect(run).toHaveBeenCalledTimes(3);
  });

  it('drops a waiting request when cancelled, and can be cancelled when idle', () => {
    vi.useFakeTimers();
    const run = vi.fn();
    const throttled = throttle(run, 600);
    throttled.cancel();

    throttled.call();
    throttled.call();
    throttled.cancel();
    vi.advanceTimersByTime(1200);
    expect(run).toHaveBeenCalledTimes(1);

    throttled.call();
    expect(run).toHaveBeenCalledTimes(2);
  });
});
