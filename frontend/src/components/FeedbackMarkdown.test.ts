import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { defineComponent, h } from 'vue';

import FeedbackMarkdown from '@/components/FeedbackMarkdown.vue';
import { renderFeedbackText } from '@/feedback-text/markdown';

const SAMPLE = [
  'Hello **world**, *you* and `code`',
  'next line',
  '',
  '- one',
  '  - nested',
  '  1. sub',
  '3. three',
  '1. four',
  '',
  '> quoted',
  '> - in a quote',
  '',
  '```',
  '<b>&amp;</b>',
  '```',
  '[label](https://x.nl/a) https://x.nl/b',
].join('\n');

/** What the widget's DOM builder makes of the same text, with its links in a new tab like the SPA's. */
function domBuilder(text: string): string {
  const box = document.createElement('div');
  box.appendChild(renderFeedbackText(text, document));
  return box.innerHTML;
}

function rendered(wrapper: ReturnType<typeof mount>): Element {
  return wrapper.find('nldd-rich-text').element;
}

describe('FeedbackMarkdown', () => {
  it('renders the same elements as the widget, inside an nldd-rich-text', () => {
    const wrapper = mount(FeedbackMarkdown, { props: { text: SAMPLE } });
    expect(wrapper.element.tagName.toLowerCase()).toBe('nldd-rich-text');
    expect(rendered(wrapper).innerHTML).toBe(domBuilder(SAMPLE));
    expect(rendered(wrapper).querySelector(':scope > ol')?.getAttribute('start')).toBe('3');
    expect(rendered(wrapper).querySelector('li ol')?.hasAttribute('start')).toBe(false);
  });

  it('shows the address as the text of every link, opening in a new tab', () => {
    const wrapper = mount(FeedbackMarkdown, { props: { text: '[label](https://x.nl/a)' } });
    const link = wrapper.get('a');
    expect(link.text()).toBe('https://x.nl/a');
    expect(link.attributes()).toEqual({
      href: 'https://x.nl/a',
      rel: 'noopener noreferrer nofollow ugc',
      dir: 'ltr',
      target: '_blank',
    });
    expect(wrapper.text()).toBe('label (https://x.nl/a)');
  });

  it('renders hostile text as text', () => {
    const text = '<img src=x onerror=alert(1)> <script>alert(1)</script> [x](javascript:alert(1)) ![i](https://x.nl/i.png)';
    const wrapper = mount(FeedbackMarkdown, { props: { text } });
    expect(rendered(wrapper).innerHTML).toBe(
      '<p>&lt;img src=x onerror=alert(1)&gt; &lt;script&gt;alert(1)&lt;/script&gt; ' +
        '[x](javascript:alert(1)) ![i](https://x.nl/i.png)</p>',
    );
    expect(wrapper.findAll('img, script, a')).toHaveLength(0);
  });

  it('follows a new text', async () => {
    const wrapper = mount(FeedbackMarkdown, { props: { text: '**first**' } });
    expect(rendered(wrapper).innerHTML).toBe('<p><strong>first</strong></p>');
    await wrapper.setProps({ text: '- second\n- third' });
    expect(rendered(wrapper).innerHTML).toBe('<ul><li>second</li><li>third</li></ul>');
    await wrapper.setProps({ text: '' });
    expect(rendered(wrapper).innerHTML).toBe('');
  });

  it('leaves the rich text out when bare, for a place inside one already', async () => {
    const Host = defineComponent({
      props: { text: { type: String, required: true } },
      setup: (props) => () => h('nldd-rich-text', [h(FeedbackMarkdown, { text: props.text, bare: true }), h('p', 'after')]),
    });
    const wrapper = mount(Host, { props: { text: 'one\n\n- two' } });
    expect(wrapper.findAll('nldd-rich-text')).toHaveLength(1);
    // The blocks are the outer rich text's own children, so it spaces them.
    expect(wrapper.element.innerHTML).toBe('<p>one</p><ul><li>two</li></ul><p>after</p>');
    await wrapper.setProps({ text: '**three**' });
    expect(wrapper.element.innerHTML).toBe('<p><strong>three</strong></p><p>after</p>');
  });

  it('passes an attribute such as a test id on to the rich text', () => {
    const wrapper = mount(FeedbackMarkdown, { props: { text: 'a' }, attrs: { 'data-testid': 'feedback-text' } });
    expect(wrapper.attributes('data-testid')).toBe('feedback-text');
  });
});
