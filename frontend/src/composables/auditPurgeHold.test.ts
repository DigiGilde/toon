import { afterEach, describe, expect, it, vi } from 'vitest';

import * as toonApi from '@/api/toon';
import type { AuditPurgeHold } from '@/api/types';

import {
  _resetAuditPurgeHold,
  auditPurgeHoldStatus,
  loadAuditPurgeHold,
  setAuditPurgeHold,
} from './auditPurgeHold';

vi.mock('@/api/toon', () => ({ auditPurgeHold: vi.fn() }));

const RUNNING: AuditPurgeHold = { onHold: false, hold: null };
const HELD: AuditPurgeHold = {
  onHold: true,
  hold: { reason: 'ticket 4521', startedAt: '2026-11-14T09:30:00Z', startedByName: 'Bea Heerder' },
};

/** A read that answers when the test says so. */
function deferred(): { answer: (status: AuditPurgeHold) => void; fail: (error: unknown) => void } {
  let answer!: (status: AuditPurgeHold) => void;
  let fail!: (error: unknown) => void;
  vi.mocked(toonApi.auditPurgeHold).mockReturnValueOnce(
    new Promise((resolve, reject) => {
      answer = resolve;
      fail = reject;
    }),
  );
  return { answer, fail };
}

describe('audit purge hold status', () => {
  afterEach(() => {
    _resetAuditPurgeHold();
    vi.mocked(toonApi.auditPurgeHold).mockReset();
  });

  it('knows nothing until a read has come in', () => {
    expect(auditPurgeHoldStatus().value).toBeNull();
  });

  it('keeps what a read answers', async () => {
    vi.mocked(toonApi.auditPurgeHold).mockResolvedValueOnce(HELD);

    expect(await loadAuditPurgeHold()).toEqual(HELD);
    expect(auditPurgeHoldStatus().value).toEqual(HELD);
  });

  it('lets two callers share one read while it is under way', async () => {
    const read = deferred();

    const first = loadAuditPurgeHold();
    const second = loadAuditPurgeHold();
    read.answer(HELD);

    expect(await first).toEqual(HELD);
    expect(await second).toEqual(HELD);
    expect(toonApi.auditPurgeHold).toHaveBeenCalledTimes(1);
  });

  it('reads anew once the earlier read is done', async () => {
    vi.mocked(toonApi.auditPurgeHold).mockResolvedValueOnce(HELD).mockResolvedValueOnce(RUNNING);

    await loadAuditPurgeHold();
    await loadAuditPurgeHold();

    expect(toonApi.auditPurgeHold).toHaveBeenCalledTimes(2);
    expect(auditPurgeHoldStatus().value).toEqual(RUNNING);
  });

  it('keeps the last known status when a read fails, and tries again on the next call', async () => {
    setAuditPurgeHold(HELD);
    vi.mocked(toonApi.auditPurgeHold).mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await expect(loadAuditPurgeHold()).rejects.toThrow('Failed to fetch');
    expect(auditPurgeHoldStatus().value).toEqual(HELD);

    vi.mocked(toonApi.auditPurgeHold).mockResolvedValueOnce(RUNNING);
    await loadAuditPurgeHold();
    expect(auditPurgeHoldStatus().value).toEqual(RUNNING);
  });

  it('takes the answer of a pause or a resume as the status', () => {
    setAuditPurgeHold(HELD);
    expect(auditPurgeHoldStatus().value).toEqual(HELD);

    setAuditPurgeHold(RUNNING);
    expect(auditPurgeHoldStatus().value).toEqual(RUNNING);
  });

  it('lets a read that was forgotten meanwhile write nothing back', async () => {
    const stale = deferred();
    const forgotten = loadAuditPurgeHold();
    _resetAuditPurgeHold();

    expect(auditPurgeHoldStatus().value).toBeNull();
    vi.mocked(toonApi.auditPurgeHold).mockResolvedValueOnce(RUNNING);
    await loadAuditPurgeHold();
    stale.answer(HELD);
    expect(await forgotten).toEqual(HELD);

    expect(auditPurgeHoldStatus().value).toEqual(RUNNING);
  });

  it('does not let a forgotten read that fails clear the read after it', async () => {
    const stale = deferred();
    const forgotten = loadAuditPurgeHold();
    _resetAuditPurgeHold();

    const fresh = deferred();
    const current = loadAuditPurgeHold();
    stale.fail(new TypeError('Failed to fetch'));
    await expect(forgotten).rejects.toThrow('Failed to fetch');

    // Still under way, so a third caller joins it.
    const joined = loadAuditPurgeHold();
    fresh.answer(HELD);
    expect(await joined).toEqual(HELD);
    expect(await current).toEqual(HELD);
    expect(toonApi.auditPurgeHold).toHaveBeenCalledTimes(2);
  });
});
