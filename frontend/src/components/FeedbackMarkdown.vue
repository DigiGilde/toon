<script lang="ts">
/**
 * A feedback comment or reply in the admin SPA: the tree of
 * feedback-text/markdown.ts rendered with Vue's `h()` inside an
 * nldd-rich-text, which styles every element the tree can hold (p, ul, ol,
 * li, pre, code, blockquote, strong, em, a). The tree is the only way in: no
 * string from it ever goes in as markup, and a new `text` renders anew.
 *
 * `bare` leaves the nldd-rich-text out, for a place inside one already: the
 * rich text spaces its direct children only, and one inside another would
 * make the text a single block of the outer one.
 */
import { computed, defineComponent, h, type VNode } from 'vue';

import { LINK_REL, parseFeedbackText, type Block, type Inline } from '@/feedback-text/markdown';

function inline(nodes: Inline[]): (VNode | string)[] {
  return nodes.map((node) => {
    switch (node.type) {
      case 'text':
        return node.value;
      case 'break':
        return h('br');
      case 'code':
        return h('code', node.value);
      case 'strong':
        return h('strong', inline(node.children));
      case 'emphasis':
        return h('em', inline(node.children));
      case 'link':
        // The admin SPA has an origin of its own, so a new tab is safe here.
        return h('a', { href: node.href, rel: LINK_REL, dir: 'ltr', target: '_blank' }, node.text);
    }
  });
}

function block(node: Block): VNode {
  switch (node.type) {
    case 'paragraph':
      return h('p', inline(node.children));
    case 'codeBlock':
      return h('pre', [h('code', node.value)]);
    case 'quote':
      return h('blockquote', node.children.map(block));
    case 'list':
      return h(
        node.ordered ? 'ol' : 'ul',
        { start: node.start === 1 ? undefined : node.start },
        node.items.map((item) => h('li', [...inline(item.children), ...item.lists.map(block)])),
      );
  }
}

export default defineComponent({
  name: 'FeedbackMarkdown',
  props: {
    /** The comment or reply as it was typed. */
    text: { type: String, required: true },
    /** The blocks without an nldd-rich-text around them. */
    bare: { type: Boolean, default: false },
  },
  setup(props) {
    const blocks = computed(() => parseFeedbackText(props.text));
    return () => (props.bare ? blocks.value.map(block) : h('nldd-rich-text', blocks.value.map(block)));
  },
});
</script>
