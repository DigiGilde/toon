import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { expectNoAxeViolations } from '../../tests/a11y';
import { ApiError } from '@/api/client';
import * as toonApi from '@/api/toon';
import type { AuditPurgeHold as PurgeStatus } from '@/api/types';
import {
  _resetAuditPurgeHold,
  auditPurgeHoldStatus,
  loadAuditPurgeHold,
} from '@/composables/auditPurgeHold';
import { formatTimestamp } from '@/format';
import { _setLocaleForTest } from '@/i18n';

import AuditPurgeHold from './AuditPurgeHold.vue';
import { fireDetailEvent } from './site/testHelpers';

// jsdom has no dialog: nldd-modal-dialog calls showModal() and close().
{
  const proto = HTMLDialogElement.prototype;
  proto.showModal ??= function showModal(this: HTMLDialogElement) {
    this.setAttribute('open', '');
  };
  proto.close ??= function close(this: HTMLDialogElement) {
    this.removeAttribute('open');
  };
}

vi.mock('@/api/toon', () => ({
  auditPurgeHold: vi.fn(),
  startAuditPurgeHold: vi.fn(),
  endAuditPurgeHold: vi.fn(),
}));

const REASON = 'onderzoek naar melding 2026-091';
const STARTED_AT = '2026-11-14T09:30:00Z';

const RUNNING: PurgeStatus = { onHold: false, hold: null };
const HELD: PurgeStatus = {
  onHold: true,
  hold: { reason: REASON, startedAt: STARTED_AT, startedByName: 'Robbert Bos' },
};

function refusal(status: number, title: string, detail?: string, code?: string): ApiError {
  return new ApiError({ type: 'about:blank', title, status, detail, code });
}

function property(el: Element, name: string): string {
  return (el as unknown as Record<string, string | undefined>)[name] ?? '';
}

async function mountWith(status: PurgeStatus) {
  vi.mocked(toonApi.auditPurgeHold).mockResolvedValue(status);
  const wrapper = mount(AuditPurgeHold, {
    attachTo: document.body,
    global: { stubs: { teleport: true } },
  });
  await flushPromises();
  return wrapper;
}

type Wrapper = Awaited<ReturnType<typeof mountWith>>;

function type(wrapper: Wrapper, value: string): void {
  fireDetailEvent(wrapper.find('[data-testid="audit-purge-reason"]').element, 'input', { value });
}

async function submit(wrapper: Wrapper): Promise<void> {
  await wrapper.find('nldd-form').trigger('submit');
  await flushPromises();
}

function modalOpen(wrapper: Wrapper): boolean {
  return wrapper.findComponent({ name: 'ConfirmModal' }).props('open') as boolean;
}

async function confirm(wrapper: Wrapper): Promise<void> {
  await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
  await flushPromises();
}

/** The dialog reports that it is gone, which is when the focus may move. */
async function dialogGone(wrapper: Wrapper): Promise<void> {
  await wrapper.find('nldd-modal-dialog').trigger('close');
  await flushPromises();
}

describe('AuditPurgeHold', () => {
  afterEach(() => {
    vi.mocked(toonApi.auditPurgeHold).mockReset();
    vi.mocked(toonApi.startAuditPurgeHold).mockReset();
    vi.mocked(toonApi.endAuditPurgeHold).mockReset();
    _resetAuditPurgeHold();
  });

  it('shows an indicator while the status is being fetched', () => {
    vi.mocked(toonApi.auditPurgeHold).mockReturnValue(new Promise(() => {}));
    const wrapper = mount(AuditPurgeHold);

    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(true);
    expect(wrapper.find('nldd-form').exists()).toBe(false);
  });

  describe('while the purge runs', () => {
    it('explains the purge and offers to pause it, in Dutch', async () => {
      const wrapper = await mountWith(RUNNING);

      expect(wrapper.find('h2').text()).toBe('Opruimen auditlog');
      expect(wrapper.find('nldd-rich-text').text()).toContain('Elke nacht');
      expect(wrapper.find('nldd-rich-text').text()).toContain('beveiligingsincident');
      const field = wrapper.find('nldd-form-field').element;
      expect(property(field, 'label')).toBe('Reden om te pauzeren');
      expect(property(wrapper.find('[data-testid="audit-purge-action"]').element, 'text')).toBe(
        'Opruimen pauzeren',
      );
      expect(wrapper.find('[data-testid="audit-purge-status"]').text()).toBe('');
    });

    it('says the same in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await mountWith(RUNNING);

      expect(wrapper.find('h2').text()).toBe('Audit purge');
      expect(wrapper.find('nldd-rich-text').text()).toContain('Every night');
      expect(property(wrapper.find('nldd-form-field').element, 'label')).toBe(
        'Reason for pausing',
      );
      expect(property(wrapper.find('[data-testid="audit-purge-action"]').element, 'text')).toBe(
        'Pause purge',
      );
    });

    it('ties a multi-line field to its label and says how long the reason is kept', async () => {
      const wrapper = await mountWith(RUNNING);

      const field = wrapper.find('[data-testid="audit-purge-reason"]');
      expect(field.element.tagName.toLowerCase()).toBe('nldd-multi-line-text-field');
      expect(property(field.element, 'inputId')).toBe('audit-purge-reason');
      expect(property(field.element, 'rows')).toBe(3);
      expect(property(wrapper.find('nldd-form-field').element, 'supportingLabel')).toBe(
        'Blijft voorgoed bij de pauze bewaard en 3 jaar in het auditlog. Platformbeheerders kunnen dit lezen.',
      );
    });

    it('says the retention in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await mountWith(RUNNING);

      expect(property(wrapper.find('nldd-form-field').element, 'supportingLabel')).toBe(
        'Kept with the pause for good, and in the audit log for 3 years. Platform administrators can read it.',
      );
    });

    it('shows an e-mail error as soon as an address is typed and clears it when it is gone', async () => {
      const wrapper = await mountWith(RUNNING);
      const field = wrapper.find('[data-testid="audit-purge-reason"]');
      expect(wrapper.find('nldd-validation-item').text()).toBe('Geef een reden.');

      type(wrapper, 'mail a@b.nl');
      await flushPromises();

      expect(field.attributes('invalid')).toBeDefined();
      expect(field.attributes('unmet')).toBeTruthy();
      expect(wrapper.findAll('nldd-validation-item')).toHaveLength(1);
      expect(wrapper.find('nldd-validation-item').text()).toBe(
        'Geen e-mailadres: Toon bewaart deze reden voorgoed en kan hem niet wissen.',
      );

      type(wrapper, '');
      await flushPromises();
      expect(field.attributes('invalid')).toBeUndefined();
      expect(field.attributes('unmet')).toBeUndefined();
      expect(wrapper.find('nldd-validation-item').text()).toBe('Geef een reden.');
    });

    it('says the e-mail error in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await mountWith(RUNNING);

      type(wrapper, 'a@b.nl');
      await flushPromises();

      expect(wrapper.find('nldd-validation-item').text()).toBe(
        'No e-mail address: Toon keeps this reason for good and cannot erase it.',
      );
    });

    it('does not open the dialog while the reason holds an e-mail address, and focuses the field', async () => {
      const wrapper = await mountWith(RUNNING);
      const focus = vi.spyOn(wrapper.find('[data-testid="audit-purge-reason"]').element as HTMLElement, 'focus');

      type(wrapper, 'mail mij op a@b.nl svp');
      await submit(wrapper);

      expect(modalOpen(wrapper)).toBe(false);
      expect(toonApi.startAuditPurgeHold).not.toHaveBeenCalled();
      expect(focus).toHaveBeenCalled();
    });

    it('sends a multi-line reason trimmed, with its line breaks', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockResolvedValue(HELD);
      const wrapper = await mountWith(RUNNING);

      type(wrapper, `\n  onderzoek naar melding\nzaak 2026-091  \n`);
      await submit(wrapper);
      await confirm(wrapper);

      expect(toonApi.startAuditPurgeHold).toHaveBeenCalledWith('onderzoek naar melding\nzaak 2026-091');
    });

    it('keeps the line breaks of the open reason on screen', async () => {
      const wrapper = await mountWith({
        onHold: true,
        hold: { reason: 'regel een\nregel twee', startedAt: STARTED_AT, startedByName: 'Robbert Bos' },
      });

      const line = wrapper.find('[role="status"] p.reason');
      expect(line.text()).toBe('Reden: regel een\nregel twee');
    });

    it('asks for a reason without opening the dialog', async () => {
      const wrapper = await mountWith(RUNNING);
      const focus = vi.spyOn(wrapper.find('[data-testid="audit-purge-reason"]').element as HTMLElement, 'focus');

      type(wrapper, '');
      await submit(wrapper);

      expect(modalOpen(wrapper)).toBe(false);
      expect(toonApi.startAuditPurgeHold).not.toHaveBeenCalled();
      const field = wrapper.find('[data-testid="audit-purge-reason"]');
      expect(field.attributes('invalid')).toBeDefined();
      expect(field.attributes('unmet')).toBeTruthy();
      expect(wrapper.find('nldd-validation-item').text()).toBe('Geef een reden.');
      expect(focus).toHaveBeenCalled();
    });

    it('refuses a reason of only whitespace', async () => {
      const wrapper = await mountWith(RUNNING);

      type(wrapper, '  \n  ');
      await submit(wrapper);

      expect(modalOpen(wrapper)).toBe(false);
      expect(wrapper.find('[data-testid="audit-purge-reason"]').attributes('invalid')).toBeDefined();
    });

    it('takes a reason of one character', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockResolvedValue(HELD);
      const wrapper = await mountWith(RUNNING);

      type(wrapper, ' 7 ');
      await submit(wrapper);
      await confirm(wrapper);

      expect(toonApi.startAuditPurgeHold).toHaveBeenCalledWith('7');
    });

    it('lets a mention with an @ pass, since it is no e-mail address', async () => {
      const wrapper = await mountWith(RUNNING);

      type(wrapper, 'INC-1 @ops-team, zie @piet, a@b');
      await submit(wrapper);

      expect(wrapper.find('[data-testid="audit-purge-reason"]').attributes('invalid')).toBeUndefined();
      expect(modalOpen(wrapper)).toBe(true);
    });

    it('takes the refusal back only once there is a reason', async () => {
      const wrapper = await mountWith(RUNNING);
      type(wrapper, '');
      await submit(wrapper);
      const field = wrapper.find('[data-testid="audit-purge-reason"]');

      type(wrapper, '   ');
      await flushPromises();
      expect(field.attributes('invalid')).toBeDefined();

      type(wrapper, REASON);
      await flushPromises();
      expect(field.attributes('invalid')).toBeUndefined();
      expect(field.attributes('unmet')).toBeUndefined();
    });

    it('reads what a native input reports, with no detail on the event', async () => {
      const wrapper = await mountWith(RUNNING);
      const field = wrapper.find('[data-testid="audit-purge-reason"]').element as HTMLElement & {
        value?: string;
      };

      field.value = REASON;
      field.dispatchEvent(new Event('input'));
      await submit(wrapper);

      expect(modalOpen(wrapper)).toBe(true);
    });

    it('confirms before pausing, and sends the reason without its spaces', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockResolvedValue(HELD);
      const wrapper = await mountWith(RUNNING);

      type(wrapper, `  ${REASON}  `);
      await submit(wrapper);

      expect(modalOpen(wrapper)).toBe(true);
      expect(property(wrapper.find('nldd-modal-dialog').element, 'text')).toBe('Opruimen pauzeren?');
      expect(toonApi.startAuditPurgeHold).not.toHaveBeenCalled();
      // Pausing can be undone, so the confirmation is not red.
      expect(wrapper.find('[data-testid="confirm-continue"]').attributes('appearance')).toBe('secondary');

      await confirm(wrapper);

      expect(toonApi.startAuditPurgeHold).toHaveBeenCalledWith(REASON);
      expect(modalOpen(wrapper)).toBe(false);
    });

    it('keeps the purge running when the confirmation is declined', async () => {
      const wrapper = await mountWith(RUNNING);
      type(wrapper, REASON);
      await submit(wrapper);

      await wrapper.find('[data-testid="confirm-cancel"]').trigger('click');
      await flushPromises();

      expect(modalOpen(wrapper)).toBe(false);
      expect(toonApi.startAuditPurgeHold).not.toHaveBeenCalled();
    });

    it('announces the pause, shows who set it and why, and switches to resuming', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockResolvedValue(HELD);
      const wrapper = await mountWith(RUNNING);
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);

      const status = wrapper.find('[role="status"]');
      expect(status.text()).toContain(
        `Opruimen staat sinds ${formatTimestamp(STARTED_AT)} op pauze, gezet door Robbert Bos.`,
      );
      expect(status.text()).toContain(`Reden: ${REASON}`);
      expect(wrapper.text()).not.toContain('Elke nacht');
      expect(property(wrapper.find('[data-testid="audit-purge-action"]').element, 'text')).toBe(
        'Opruimen hervatten',
      );
      expect(property(wrapper.find('[data-testid="audit-purge-reason"]').element, 'value')).toBe('');
      expect(wrapper.find('[data-testid="audit-purge-error"]').exists()).toBe(false);
    });

    it('puts the focus back on the button once the dialog is gone', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockResolvedValue(HELD);
      const wrapper = await mountWith(RUNNING);
      const button = wrapper.find('[data-testid="audit-purge-action"]').element as HTMLElement;
      const focus = vi.spyOn(button, 'focus');
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);
      expect(focus).not.toHaveBeenCalled();

      await dialogGone(wrapper);
      expect(focus).toHaveBeenCalledTimes(1);

      // Said once: a later close has nothing left to do.
      await dialogGone(wrapper);
      expect(focus).toHaveBeenCalledTimes(1);
    });

    it('says in English who paused it', async () => {
      _setLocaleForTest('en');
      const wrapper = await mountWith(HELD);

      expect(wrapper.find('[role="status"]').text()).toContain(
        `The purge has been paused since ${formatTimestamp(STARTED_AT)}, by Robbert Bos.`,
      );
      expect(wrapper.find('[role="status"]').text()).toContain(`Reason: ${REASON}`);
    });
  });

  describe('while the purge is paused', () => {
    it('shows the pause, with the date in the language of the member, and offers to resume', async () => {
      const wrapper = await mountWith(HELD);

      expect(wrapper.find('[role="status"]').text()).toContain('sinds');
      expect(wrapper.find('[role="status"]').text()).toContain(formatTimestamp(STARTED_AT));
      expect(wrapper.text()).not.toContain('Elke nacht');
      expect(property(wrapper.find('[data-testid="audit-purge-action"]').element, 'text')).toBe(
        'Opruimen hervatten',
      );
      expect(property(wrapper.find('nldd-form-field').element, 'label')).toBe('Reden om te hervatten');
    });

    it('labels the reason field for resuming in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await mountWith(HELD);

      expect(property(wrapper.find('nldd-form-field').element, 'label')).toBe('Reason for resuming');
    });

    it('warns that entries will be deleted the next night before resuming', async () => {
      _setLocaleForTest('en');
      vi.mocked(toonApi.endAuditPurgeHold).mockResolvedValue(RUNNING);
      const wrapper = await mountWith(HELD);

      type(wrapper, 'incident afgehandeld, ticket 4521');
      await submit(wrapper);

      const dialog = wrapper.find('nldd-modal-dialog').element;
      expect(property(dialog, 'text')).toBe('Resume the purge?');
      expect(property(dialog, 'supportingText')).toBe(
        'Audit log entries past their retention will be deleted the next night.',
      );
      expect(wrapper.find('[data-testid="confirm-continue"]').attributes('appearance')).toBe('destructive');
      expect(toonApi.endAuditPurgeHold).not.toHaveBeenCalled();
    });

    it('resumes with the reason, says so, and goes back to offering a pause', async () => {
      vi.mocked(toonApi.endAuditPurgeHold).mockResolvedValue(RUNNING);
      const wrapper = await mountWith(HELD);

      type(wrapper, 'incident afgehandeld, ticket 4521');
      await submit(wrapper);
      await confirm(wrapper);

      expect(toonApi.endAuditPurgeHold).toHaveBeenCalledWith('incident afgehandeld, ticket 4521');
      expect(wrapper.find('[role="status"]').text()).toBe('Opruimen loopt weer.');
      expect(wrapper.find('nldd-rich-text').text()).toBe('Opruimen loopt weer.');
      expect(property(wrapper.find('[data-testid="audit-purge-action"]').element, 'text')).toBe(
        'Opruimen pauzeren',
      );
      expect(wrapper.text()).not.toContain('Reden: ');
    });

    it('does not repeat that it resumed once it is paused again', async () => {
      vi.mocked(toonApi.endAuditPurgeHold).mockResolvedValue(RUNNING);
      vi.mocked(toonApi.startAuditPurgeHold).mockResolvedValue(HELD);
      const wrapper = await mountWith(HELD);
      type(wrapper, 'incident afgehandeld, ticket 4521');
      await submit(wrapper);
      await confirm(wrapper);
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);

      expect(wrapper.find('[role="status"]').text()).not.toContain('loopt weer');
      expect(wrapper.find('[role="status"]').text()).toContain('op pauze');
    });
  });

  describe('when the server refuses', () => {
    it('shows its detail on a 422 and keeps the reason to correct, focusing the field', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockRejectedValue(
        refusal(422, 'Ongeldige invoer', 'reason is invalid'),
      );
      const wrapper = await mountWith(RUNNING);
      const focus = vi.spyOn(wrapper.find('[data-testid="audit-purge-reason"]').element as HTMLElement, 'focus');
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);
      await dialogGone(wrapper);

      const banner = wrapper.find('[data-testid="audit-purge-error"]').element;
      expect(property(banner, 'supportingText')).toBe(
        'reason is invalid',
      );
      expect(toonApi.auditPurgeHold).toHaveBeenCalledTimes(1);
      expect(property(wrapper.find('[data-testid="audit-purge-reason"]').element, 'value')).toBe(
        REASON,
      );
      expect(property(wrapper.find('[data-testid="audit-purge-action"]').element, 'text')).toBe(
        'Opruimen pauzeren',
      );
      expect(focus).toHaveBeenCalled();
      expect(modalOpen(wrapper)).toBe(false);
    });

    it('reloads the status on a 409 and shows the detail of the server', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockRejectedValue(
        refusal(409, 'Al gepauzeerd', 'The purge is already paused.'),
      );
      const wrapper = await mountWith(RUNNING);
      vi.mocked(toonApi.auditPurgeHold).mockResolvedValue(HELD);
      const button = wrapper.find('[data-testid="audit-purge-action"]').element as HTMLElement;
      const focus = vi.spyOn(button, 'focus');
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);
      await dialogGone(wrapper);

      expect(toonApi.auditPurgeHold).toHaveBeenCalledTimes(2);
      expect(property(wrapper.find('[data-testid="audit-purge-error"]').element, 'supportingText')).toBe(
        'The purge is already paused.',
      );
      expect(wrapper.find('[role="status"]').text()).toContain('op pauze');
      expect(property(button, 'text')).toBe('Opruimen hervatten');
      expect(focus).toHaveBeenCalled();
    });

    it('shows the status of the reload as the truth when the pause was ended meanwhile', async () => {
      vi.mocked(toonApi.endAuditPurgeHold).mockRejectedValue(
        refusal(409, 'Geen pauze', 'The purge is not paused.'),
      );
      const wrapper = await mountWith(HELD);
      vi.mocked(toonApi.auditPurgeHold).mockResolvedValue(RUNNING);
      type(wrapper, 'incident afgehandeld, ticket 4521');
      await submit(wrapper);
      await confirm(wrapper);

      expect(property(wrapper.find('[data-testid="audit-purge-error"]').element, 'supportingText')).toBe(
        'The purge is not paused.',
      );
      expect(property(wrapper.find('[data-testid="audit-purge-action"]').element, 'text')).toBe(
        'Opruimen pauzeren',
      );
      expect(wrapper.find('[role="status"]').text()).toBe('');
    });

    it('offers another go when the reload after a 409 fails as well', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockRejectedValue(
        refusal(409, 'Al gepauzeerd', 'The purge is already paused.'),
      );
      const wrapper = await mountWith(RUNNING);
      vi.mocked(toonApi.auditPurgeHold).mockRejectedValueOnce(
        refusal(500, 'Interne fout', 'De server viel om.'),
      );
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);

      expect(wrapper.find('[data-testid="audit-purge-retry"]').exists()).toBe(true);
      expect(wrapper.find('nldd-form').exists()).toBe(false);
    });

    it('shows a failure that is not an answer of the server, and keeps the reason', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockRejectedValue(new TypeError('Failed to fetch'));
      const wrapper = await mountWith(RUNNING);
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);

      expect(property(wrapper.find('[data-testid="audit-purge-error"]').element, 'text')).toBe(
        'Er ging iets mis',
      );
      expect(toonApi.auditPurgeHold).toHaveBeenCalledTimes(1);
      expect(property(wrapper.find('[data-testid="audit-purge-reason"]').element, 'value')).toBe(REASON);
    });

    it('clears an earlier refusal when asking again', async () => {
      vi.mocked(toonApi.startAuditPurgeHold)
        .mockRejectedValueOnce(refusal(422, 'Ongeldige invoer', 'reason is invalid'))
        .mockResolvedValueOnce(HELD);
      const wrapper = await mountWith(RUNNING);
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);
      expect(wrapper.find('[data-testid="audit-purge-error"]').exists()).toBe(true);

      await submit(wrapper);
      expect(wrapper.find('[data-testid="audit-purge-error"]').exists()).toBe(false);
      await confirm(wrapper);

      expect(wrapper.find('[role="status"]').text()).toContain('op pauze');
    });
  });

  describe('when the server refuses the reason itself', () => {
    const INVISIBLE =
      'The reason holds an invisible character, such as a zero-width space. Type the reason again or paste it as plain text.';

    async function refusedOnce(error: ApiError): Promise<Wrapper> {
      vi.mocked(toonApi.startAuditPurgeHold).mockRejectedValueOnce(error);
      const wrapper = await mountWith(RUNNING);
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);
      return wrapper;
    }

    it('shows its detail next to the field instead of in a banner, and focuses the field', async () => {
      _setLocaleForTest('en');
      vi.mocked(toonApi.startAuditPurgeHold).mockRejectedValueOnce(
        refusal(422, 'Unprocessable input', INVISIBLE, 'HOLD_REASON_INVISIBLE_CHARACTER'),
      );
      const wrapper = await mountWith(RUNNING);
      const focus = vi.spyOn(wrapper.find('[data-testid="audit-purge-reason"]').element as HTMLElement, 'focus');
      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);
      await dialogGone(wrapper);

      const field = wrapper.find('[data-testid="audit-purge-reason"]');
      const item = wrapper.find('nldd-validation-item');
      expect(item.text()).toBe(INVISIBLE);
      expect(field.attributes('invalid')).toBeDefined();
      expect(field.attributes('unmet')).toBe(item.attributes('id'));
      expect(wrapper.find('[data-testid="audit-purge-error"]').exists()).toBe(false);
      expect(wrapper.text()).not.toContain('Unprocessable input');
      expect(property(field.element, 'value')).toBe(REASON);
      expect(focus).toHaveBeenCalled();
      expect(modalOpen(wrapper)).toBe(false);
    });

    it('does not send the same refused reason again, but takes a corrected one', async () => {
      const wrapper = await refusedOnce(
        refusal(422, 'Onverwerkbare invoer', 'De reden bevat een e-mailadres.', 'HOLD_REASON_EMAIL'),
      );

      await submit(wrapper);
      expect(modalOpen(wrapper)).toBe(false);
      expect(toonApi.startAuditPurgeHold).toHaveBeenCalledTimes(1);

      vi.mocked(toonApi.startAuditPurgeHold).mockResolvedValueOnce(HELD);
      type(wrapper, 'ticket 4521');
      await flushPromises();
      const field = wrapper.find('[data-testid="audit-purge-reason"]');
      expect(field.attributes('invalid')).toBeUndefined();
      expect(wrapper.find('nldd-validation-item').text()).toBe('Geef een reden.');

      await submit(wrapper);
      await confirm(wrapper);
      expect(toonApi.startAuditPurgeHold).toHaveBeenLastCalledWith('ticket 4521');
      expect(wrapper.find('[role="status"]').text()).toContain('op pauze');
    });

    it('falls back on the title when the refusal carries no detail', async () => {
      const wrapper = await refusedOnce(
        refusal(422, 'Onverwerkbare invoer', undefined, 'HOLD_REASON_TOO_LONG'),
      );

      expect(wrapper.find('nldd-validation-item').text()).toBe('Onverwerkbare invoer');
    });

    it('leaves a 422 that is not about the reason to the banner', async () => {
      const wrapper = await refusedOnce(
        refusal(422, 'Onverwerkbare invoer', 'Het verzoek klopt niet.', 'VALIDATION_ERROR'),
      );

      expect(
        property(wrapper.find('[data-testid="audit-purge-error"]').element, 'supportingText'),
      ).toBe('Het verzoek klopt niet.');
      expect(wrapper.find('nldd-validation-item').text()).toBe('Geef een reden.');
    });
  });

  describe('sharing the status', () => {
    it('passes a pause and a resume on, so the warning elsewhere follows without a reload', async () => {
      vi.mocked(toonApi.startAuditPurgeHold).mockResolvedValue(HELD);
      vi.mocked(toonApi.endAuditPurgeHold).mockResolvedValue(RUNNING);
      const wrapper = await mountWith(RUNNING);
      expect(auditPurgeHoldStatus().value).toEqual(RUNNING);

      type(wrapper, REASON);
      await submit(wrapper);
      await confirm(wrapper);
      expect(auditPurgeHoldStatus().value).toEqual(HELD);

      type(wrapper, 'incident afgehandeld, ticket 4521');
      await submit(wrapper);
      await confirm(wrapper);
      expect(auditPurgeHoldStatus().value).toEqual(RUNNING);
      expect(toonApi.auditPurgeHold).toHaveBeenCalledTimes(1);
    });

    it('joins a read already under way instead of firing a second', async () => {
      let answer: (status: PurgeStatus) => void = () => undefined;
      vi.mocked(toonApi.auditPurgeHold).mockReturnValue(
        new Promise((resolve) => {
          answer = resolve;
        }),
      );
      void loadAuditPurgeHold();
      const wrapper = mount(AuditPurgeHold);

      answer(HELD);
      await flushPromises();

      expect(toonApi.auditPurgeHold).toHaveBeenCalledTimes(1);
      expect(property(wrapper.find('[data-testid="audit-purge-action"]').element, 'text')).toBe(
        'Opruimen hervatten',
      );
    });
  });

  describe('when the status cannot be fetched', () => {
    it('shows the refusal with a way to try again', async () => {
      vi.mocked(toonApi.auditPurgeHold).mockRejectedValue(
        refusal(500, 'Interne fout', 'De server viel om.'),
      );
      const wrapper = mount(AuditPurgeHold);
      await flushPromises();

      expect(property(wrapper.find('nldd-banner').element, 'text')).toBe('Interne fout');
      expect(wrapper.find('nldd-form').exists()).toBe(false);

      vi.mocked(toonApi.auditPurgeHold).mockResolvedValue(RUNNING);
      await wrapper.find('[data-testid="audit-purge-retry"]').trigger('click');
      await flushPromises();

      expect(wrapper.find('nldd-banner').exists()).toBe(false);
      expect(wrapper.find('nldd-form').exists()).toBe(true);
    });
  });

  it('has no axe violations, running or paused', async () => {
    const running = await mountWith(RUNNING);
    await expectNoAxeViolations(running.element);
    running.unmount();

    const paused = await mountWith(HELD);
    await expectNoAxeViolations(paused.element);
  });
});
