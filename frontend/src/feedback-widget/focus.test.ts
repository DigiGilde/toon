import { describe, expect, it } from 'vitest';

import { FocusPlan } from './focus';

describe('the focus plan', () => {
  it('sends focus to the target of each step', () => {
    const plan = new FocusPlan();
    expect(plan.targets('idle', 'open', 'menu')).toEqual(['heading']);
    expect(plan.targets('menu', 'point', 'placing')).toEqual(['overlay']);
    expect(plan.targets('placing', 'placed', 'composing')).toEqual(['comment']);
    expect(plan.targets('composing', 'region', 'cropping')).toEqual(['overlay']);
    expect(plan.targets('composing', 'send', 'sending')).toEqual([]);
    expect(plan.targets('sending', 'sent', 'sent')).toEqual(['heading']);
    expect(plan.targets('menu', 'mine', 'mine')).toEqual(['heading']);
    expect(plan.targets('hiding', 'session', 'hideConfirm')).toEqual(['cancel']);
    expect(plan.targets('hiding', 'minute', 'hidden')).toEqual([]);
  });

  it('leaves focus alone after a failed send and when the button comes back', () => {
    const plan = new FocusPlan();
    expect(plan.targets('sending', 'failed', 'composing')).toEqual([]);
    expect(plan.targets('hidden', 'show', 'idle')).toEqual([]);
  });

  it('returns focus to the button when the panel closes', () => {
    const plan = new FocusPlan();
    plan.opened('composing', 'menu.point');
    expect(plan.targets('composing', 'close', 'idle')).toEqual(['trigger']);
  });

  it('returns focus to the control that opened a step, then to the step itself', () => {
    const plan = new FocusPlan();
    plan.opened('placing', 'menu.point');
    plan.opened('cropping', 'shot.region');
    expect(plan.targets('placing', 'back', 'menu')).toEqual(['menu.point', 'heading']);
    expect(plan.targets('cropping', 'done', 'composing')).toEqual(['shot.region', 'comment']);
    expect(plan.targets('mine', 'back', 'menu')).toEqual(['heading']);
  });

  it('remembers the latest opener', () => {
    const plan = new FocusPlan();
    plan.opened('composing', 'menu.point');
    plan.opened('composing', 'menu.text');
    expect(plan.targets('composing', 'back', 'menu')).toEqual(['menu.text', 'heading']);
  });

  it('has no target of its own in a step without one', () => {
    const plan = new FocusPlan();
    plan.opened('hidden', 'hide.minute');
    expect(plan.targets('hidden', 'back', 'hidden')).toEqual(['hide.minute']);
  });
});
