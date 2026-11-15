/**
 * The status of the audit purge pause, shared by the switch on the platform
 * page and the warning the shell shows on every other page. One module-level
 * value, so that a pause or a resume on the platform page reaches the warning
 * without a reload, and the two do not each fire their own read.
 *
 * Platform administrators only: the endpoint refuses everyone else, so a
 * caller checks the role before it loads.
 */
import { readonly, ref } from 'vue';

import { auditPurgeHold } from '@/api/toon';
import type { AuditPurgeHold } from '@/api/types';

const status = ref<AuditPurgeHold | null>(null);
let pending: Promise<AuditPurgeHold> | null = null;

/**
 * Reads the status anew, or joins the read already under way. A failed read
 * leaves the last known status alone and rejects, so the caller decides
 * whether that is worth telling.
 */
export function loadAuditPurgeHold(): Promise<AuditPurgeHold> {
  if (!pending) {
    // A read that was forgotten meanwhile (a reset) writes nothing back.
    const read: Promise<AuditPurgeHold> = auditPurgeHold()
      .then((answer) => {
        if (pending === read) status.value = answer;
        return answer;
      })
      .finally(() => {
        if (pending === read) pending = null;
      });
    pending = read;
  }
  return pending;
}

/** The answer to a pause or a resume, which is the status from then on. */
export function setAuditPurgeHold(answer: AuditPurgeHold): void {
  status.value = answer;
}

/** Null until a read has come in. */
export function auditPurgeHoldStatus() {
  return readonly(status);
}

/** For tests only: forgets the status, and any read under way writes nothing back. */
export function _resetAuditPurgeHold(): void {
  status.value = null;
  pending = null;
}
