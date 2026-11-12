/**
 * At most one call per `wait` milliseconds, and the last request always gets
 * its turn: what the crosshair and frame announcements need, so a screen
 * reader hears where the crosshair ended up without hearing every step.
 */
export interface Throttled {
  call(): void;
  cancel(): void;
}

export function throttle(run: () => void, wait: number): Throttled {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending = false;
  const fire = () => {
    run();
    timer = setTimeout(() => {
      timer = null;
      if (pending) {
        pending = false;
        fire();
      }
    }, wait);
  };
  return {
    call() {
      if (timer === null) fire();
      else pending = true;
    },
    cancel() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      pending = false;
    },
  };
}
