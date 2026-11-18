import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  HIDDEN_CLASS,
  LINK_REL,
  TEXT_CLASS,
  feedbackPlainText,
  parseFeedbackText,
  renderFeedbackText,
  safeLink,
  type Block,
  type Inline,
  type ListItem,
  type RenderOptions,
} from './markdown';
import { FEEDBACK_TEXT_STYLES } from './styles';

const HERE = dirname(fileURLToPath(import.meta.url));

// Characters that would be invisible in this file, spelled out.
const RLO = String.fromCodePoint(0x202e);
const LRI = String.fromCodePoint(0x2066);
const ZWSP = String.fromCodePoint(0x200b);
const ZWJ = String.fromCodePoint(0x200d);
const SOFT_HYPHEN = String.fromCodePoint(0xad);
const BOM = String.fromCodePoint(0xfeff);
const HANGUL_FILLER = String.fromCodePoint(0x3164);
const VARIATION = String.fromCodePoint(0xfe0f);
const TAG_VARIATION = String.fromCodePoint(0xe0100);
const CYRILLIC_A = String.fromCodePoint(0x430);
const GRIN = String.fromCodePoint(0x1f600);

// Builders for the expected trees.
const t = (value: string): Inline => ({ type: 'text', value });
const br: Inline = { type: 'break' };
const code = (value: string): Inline => ({ type: 'code', value });
const strong = (...children: Inline[]): Inline => ({ type: 'strong', children });
const em = (...children: Inline[]): Inline => ({ type: 'emphasis', children });
const link = (href: string, text: string): Inline => ({ type: 'link', href, text });
const p = (...children: Inline[]): Block => ({ type: 'paragraph', children });
const pre = (value: string): Block => ({ type: 'codeBlock', value });
const quote = (...children: Block[]): Block => ({ type: 'quote', children });
const li = (children: Inline[], lists: Block[] = []): ListItem => ({ children, lists: lists as never });
const ul = (...items: ListItem[]): Block => ({ type: 'list', ordered: false, start: 1, items });
const ol = (start: number, ...items: ListItem[]): Block => ({ type: 'list', ordered: true, start, items });

/** The inline nodes of a text that is one paragraph. */
function inline(text: string): Inline[] {
  const blocks = parseFeedbackText(text);
  expect(blocks).toHaveLength(1);
  expect(blocks[0]!.type).toBe('paragraph');
  return (blocks[0] as { children: Inline[] }).children;
}

function html(text: string, options?: RenderOptions, doc: Document = document): string {
  const box = doc.createElement('div');
  box.appendChild(renderFeedbackText(text, doc, options));
  return box.innerHTML;
}

function depth(nodes: Inline[]): number {
  return Math.max(
    0,
    ...nodes.map((node) => (node.type === 'strong' || node.type === 'emphasis' ? 1 + depth(node.children) : 0)),
  );
}

describe('blocks', () => {
  it('has nothing for an empty or blank text', () => {
    expect(parseFeedbackText('')).toEqual([]);
    expect(parseFeedbackText('  \n \t\n\n')).toEqual([]);
  });

  it('splits paragraphs at a blank line and keeps the single line breaks', () => {
    expect(parseFeedbackText('one\ntwo\n\nthree')).toEqual([p(t('one'), br, t('two')), p(t('three'))]);
  });

  it('takes the spaces off the start and end of every line', () => {
    expect(parseFeedbackText('   leading\ntrailing   \n\n  both  ')).toEqual([
      p(t('leading'), br, t('trailing')),
      p(t('both')),
    ]);
  });

  it('reads Windows and old Mac line endings as line breaks', () => {
    expect(parseFeedbackText('a\r\nb\r\n\r\nc\rd')).toEqual([p(t('a'), br, t('b')), p(t('c'), br, t('d'))]);
  });

  it('keeps a heading as the text it was typed as', () => {
    expect(parseFeedbackText('# Title\n## Sub')).toEqual([p(t('# Title'), br, t('## Sub'))]);
    expect(parseFeedbackText('Title\n===')).toEqual([p(t('Title'), br, t('==='))]);
  });

  it('keeps a table and a rule as text', () => {
    expect(parseFeedbackText('| a | b |\n|---|---|\n---')).toEqual([
      p(t('| a | b |'), br, t('|---|---|'), br, t('---')),
    ]);
  });

  it('reads -, * and + as one bullet list', () => {
    expect(parseFeedbackText('- one\n* two\n+ three')).toEqual([
      ul(li([t('one')]), li([t('two')]), li([t('three')])),
    ]);
  });

  it('reads 1. and 1) as a numbered list, from the number typed first', () => {
    expect(parseFeedbackText('1. one\n2) two')).toEqual([ol(1, li([t('one')]), li([t('two')]))]);
    expect(parseFeedbackText('3. three\n4. four')).toEqual([ol(3, li([t('three')]), li([t('four')]))]);
    expect(parseFeedbackText('007. bond')).toEqual([ol(7, li([t('bond')]))]);
  });

  it('takes a tab after the marker, and an item with nothing after it', () => {
    expect(parseFeedbackText('-\tone\n-')).toEqual([ul(li([t('one')]), li([]))]);
  });

  it('needs a space after the marker, and at most nine digits', () => {
    expect(parseFeedbackText('-one')).toEqual([p(t('-one'))]);
    expect(parseFeedbackText('1.5 litre')).toEqual([p(t('1.5 litre'))]);
    expect(parseFeedbackText('1234567890. ten digits')).toEqual([p(t('1234567890. ten digits'))]);
    expect(parseFeedbackText('10 things')).toEqual([p(t('10 things'))]);
    expect(parseFeedbackText('+31 6 12345678')).toEqual([p(t('+31 6 12345678'))]);
  });

  it('starts a list on the line after a sentence', () => {
    expect(parseFeedbackText('Wrong:\n- one\n- two')).toEqual([
      p(t('Wrong:')),
      ul(li([t('one')]), li([t('two')])),
    ]);
    expect(parseFeedbackText('Steps:\n1. one')).toEqual([p(t('Steps:')), ol(1, li([t('one')]))]);
  });

  it('keeps a number that does not start at 1, and an empty marker, inside the sentence', () => {
    expect(parseFeedbackText('In\n2024. was a year')).toEqual([p(t('In'), br, t('2024. was a year'))]);
    expect(parseFeedbackText('Regards,\n-')).toEqual([p(t('Regards,'), br, t('-'))]);
  });

  it('nests an item indented by two or more, one level deep at most', () => {
    expect(parseFeedbackText('- a\n  - b\n    - c\n        - d\n- e')).toEqual([
      ul(li([t('a')], [ul(li([t('b')]), li([t('c')]), li([t('d')]))]), li([t('e')])),
    ]);
    expect(parseFeedbackText('1. a\n   1. b')).toEqual([ol(1, li([t('a')], [ol(1, li([t('b')]))]))]);
  });

  it('measures a tab as four columns', () => {
    expect(parseFeedbackText('- a\n\t- b')).toEqual([ul(li([t('a')], [ul(li([t('b')]))]))]);
    expect(parseFeedbackText('- a\n \t- b')).toEqual([ul(li([t('a')], [ul(li([t('b')]))]))]);
  });

  it('nests an indented list under an indented first item only from two columns further in', () => {
    expect(parseFeedbackText('  - a\n   - b\n    - c')).toEqual([
      ul(li([t('a')]), li([t('b')], [ul(li([t('c')]))])),
    ]);
  });

  it('starts a new list where the kind of marker changes', () => {
    expect(parseFeedbackText('- a\n1. b')).toEqual([ul(li([t('a')])), ol(1, li([t('b')]))]);
    expect(parseFeedbackText('- a\n  - b\n  2. c')).toEqual([
      ul(li([t('a')], [ul(li([t('b')])), ol(2, li([t('c')]))])),
    ]);
  });

  it('adds a following line to the last item, nested or not, indented or not', () => {
    expect(parseFeedbackText('- a\n  more\nlazy')).toEqual([ul(li([t('a'), br, t('more'), br, t('lazy')]))]);
    expect(parseFeedbackText('- a\n  - b\n    more')).toEqual([ul(li([t('a')], [ul(li([t('b'), br, t('more')]))]))]);
    expect(parseFeedbackText('- \n  only')).toEqual([ul(li([t('only')]))]);
  });

  it('keeps one list across a blank line between items, and ends it at a blank line before text', () => {
    expect(parseFeedbackText('- a\n\n- b\n\n  - c')).toEqual([ul(li([t('a')]), li([t('b')], [ul(li([t('c')]))]))]);
    expect(parseFeedbackText('- a\n\ntext')).toEqual([ul(li([t('a')])), p(t('text'))]);
  });

  it('reads a fenced code block as typed, ignoring its info string', () => {
    expect(parseFeedbackText("```js\nconst a = '<b>';\n  **not bold**\n\n```")).toEqual([
      pre("const a = '<b>';\n  **not bold**\n"),
    ]);
  });

  it('closes a fence only with as many backticks or more, and nothing after them', () => {
    expect(parseFeedbackText('````\n```\n``` not\n`````  \nafter')).toEqual([pre('```\n``` not'), p(t('after'))]);
  });

  it('runs an unclosed fence to the end', () => {
    expect(parseFeedbackText('text\n```\ncode\n\nmore')).toEqual([p(t('text')), pre('code\n\nmore')]);
    expect(parseFeedbackText('```')).toEqual([pre('')]);
  });

  it('takes the fence indentation off the code lines, spaces and tabs', () => {
    expect(parseFeedbackText('  ```\n  a\n    b\n c\n  ```')).toEqual([pre('a\n  b\nc')]);
    expect(parseFeedbackText('\t```\n\tcode\n  \tmixed\n\t```')).toEqual([pre('code\nmixed')]);
  });

  it('reads backticks with a backtick after them as inline code, not as a fence', () => {
    expect(parseFeedbackText('```x``` y')).toEqual([p(code('x'), t(' y'))]);
  });

  it('ends a list where a fence or a quote starts', () => {
    expect(parseFeedbackText('- a\n  ```\n  code\n  ```\n- b')).toEqual([
      ul(li([t('a')])),
      pre('code'),
      ul(li([t('b')])),
    ]);
    expect(parseFeedbackText('- a\n> b')).toEqual([ul(li([t('a')])), quote(p(t('b')))]);
  });

  it('reads a blockquote, with paragraphs, a list and code inside it', () => {
    expect(parseFeedbackText('> a\n>b\n>\n> - c\n> ```\n> <d>\n> ```')).toEqual([
      quote(p(t('a'), br, t('b')), ul(li([t('c')])), pre('<d>')),
    ]);
  });

  it('starts a quote inside a paragraph, and ends it at the first line without >', () => {
    expect(parseFeedbackText('said:\n   > a\nb')).toEqual([p(t('said:')), quote(p(t('a'))), p(t('b'))]);
  });

  it('keeps a quote inside a quote as text', () => {
    expect(parseFeedbackText('> > b\n>>c')).toEqual([quote(p(t('> b'), br, t('>c')))]);
  });
});

describe('inline', () => {
  it('reads strong and emphasis, with * and with _', () => {
    expect(inline('**a** *b* _c_ __d__')).toEqual([strong(t('a')), t(' '), em(t('b')), t(' '), em(t('c')), t(' '), strong(t('d'))]);
  });

  it('nests them', () => {
    expect(inline('**a *b* c**')).toEqual([strong(t('a '), em(t('b')), t(' c'))]);
    expect(inline('***x***')).toEqual([em(strong(t('x')))]);
    expect(inline('***x** y*')).toEqual([em(strong(t('x')), t(' y'))]);
  });

  it('follows the rule of three for runs that can open and close', () => {
    expect(inline('*foo**bar**baz*')).toEqual([em(t('foo'), strong(t('bar')), t('baz'))]);
    expect(inline('*foo**bar*')).toEqual([em(t('foo**bar'))]);
    expect(inline('foo***bar***baz')).toEqual([t('foo'), em(strong(t('bar'))), t('baz')]);
  });

  it('reads * inside a word and keeps _ inside a word as text', () => {
    expect(inline('a*b*c')).toEqual([t('a'), em(t('b')), t('c')]);
    expect(inline('snake_case_name')).toEqual([t('snake_case_name')]);
  });

  it('keeps unbalanced markers as text', () => {
    expect(inline('**a')).toEqual([t('**a')]);
    expect(inline('a**')).toEqual([t('a**')]);
    expect(inline('_a *b_ c*')).toEqual([em(t('a *b')), t(' c*')]);
    expect(inline('x * a *')).toEqual([t('x * a *')]);
    expect(inline('a * b ** c _ d')).toEqual([t('a * b ** c _ d')]);
  });

  it('pairs a closer with the nearest opener of its kind, past one of the other kind', () => {
    expect(inline('*a _b*')).toEqual([em(t('a _b'))]);
    expect(inline('x* *y*')).toEqual([t('x* '), em(t('y'))]);
  });

  it('lets a run close one emphasis and open the next', () => {
    expect(inline('**a***b**')).toEqual([strong(t('a')), em(t('b')), t('*')]);
  });

  it('nests five deep at most and keeps the markers deeper than that as text', () => {
    const markers = ['*', '_', '*', '_', '*', '_', '*'];
    const text = `${markers.map((marker) => `${marker}w `).join('')}x${[...markers].reverse().map((marker) => ` w${marker}`).join('')}`;
    const nodes = inline(text);
    expect(depth(nodes)).toBe(5);
    expect(feedbackPlainText(text)).toBe('w w w w w _w *w x w* w_ w w w w w');
  });

  it('reads inline code, with backticks of its own inside a longer run', () => {
    expect(inline('`a` and ``b ` c``')).toEqual([code('a'), t(' and '), code('b ` c')]);
  });

  it('takes one space off both ends of inline code, unless it is all spaces', () => {
    expect(inline('` a `')).toEqual([code('a')]);
    expect(inline('`  `')).toEqual([code('  ')]);
    expect(inline('` a`')).toEqual([code(' a')]);
  });

  it('reads a line break inside inline code as a space', () => {
    expect(inline('`a\nb`')).toEqual([code('a b')]);
  });

  it('keeps backticks without a matching run as text', () => {
    expect(inline('``a`')).toEqual([t('``a`')]);
    expect(inline('`a``b')).toEqual([t('`a``b')]);
  });

  it('reads no markup inside inline code', () => {
    expect(inline('`*a* [x](https://x.nl) <b>`')).toEqual([code('*a* [x](https://x.nl) <b>')]);
  });

  it('takes a backslash before punctuation as an escape, and keeps it before anything else', () => {
    expect(inline('\\*a\\* \\_b\\_ \\`c\\` \\\\ \\a')).toEqual([t('*a* _b_ `c` \\ \\a')]);
    expect(inline('\\``code`')).toEqual([t('`'), code('code')]);
    expect(inline('\\```a')).toEqual([t('```a')]);
    expect(inline('end\\')).toEqual([t('end\\')]);
  });

  it('keeps HTML, scripts and event handlers as text', () => {
    expect(inline('<b>x</b> <script>alert(1)</script> <img src=x onerror=alert(1)>')).toEqual([
      t('<b>x</b> <script>alert(1)</script> <img src=x onerror=alert(1)>'),
    ]);
  });

  it('keeps an entity as typed', () => {
    expect(inline('&amp; &lt;b&gt; &#x3C;')).toEqual([t('&amp; &lt;b&gt; &#x3C;')]);
  });

  it('keeps non-ASCII and emoji, also next to markers', () => {
    expect(inline(`Één **${GRIN} kopje** café ${GRIN}*x*${GRIN}`)).toEqual([
      t('Één '),
      strong(t(`${GRIN} kopje`)),
      t(` café ${GRIN}`),
      em(t('x')),
      t(GRIN),
    ]);
  });

  it('reads lone surrogates beside a marker as characters of their own', () => {
    const low = String.fromCharCode(0xdc00);
    expect(inline(`a${low}*b*`)).toEqual([t(`a${low}`), em(t('b'))]);
    expect(inline(`${low}*b*`)).toEqual([t(low), em(t('b'))]);
  });
});

describe('links', () => {
  it('shows a label, then the address as the link', () => {
    expect(inline('see [the page](https://x.nl/a) now')).toEqual([
      t('see the page ('),
      link('https://x.nl/a', 'https://x.nl/a'),
      t(') now'),
    ]);
  });

  it('formats the label, but makes no link inside it', () => {
    expect(inline('[**b** https://a.nl](https://b.nl)')).toEqual([
      strong(t('b')),
      t(' https://a.nl ('),
      link('https://b.nl/', 'https://b.nl'),
      t(')'),
    ]);
  });

  it('shows the link alone when the label is empty or is the address', () => {
    expect(inline('[](https://x.nl)')).toEqual([link('https://x.nl/', 'https://x.nl')]);
    expect(inline('[ https://x.nl ](https://x.nl)')).toEqual([link('https://x.nl/', 'https://x.nl')]);
  });

  it('never lets a label hide where the link goes', () => {
    expect(inline('[https://bank.nl](https://evil.example)')).toEqual([
      t('https://bank.nl ('),
      link('https://evil.example/', 'https://evil.example'),
      t(')'),
    ]);
  });

  it('keeps balanced parentheses in the address', () => {
    expect(inline('[w](https://nl.wikipedia.org/wiki/A_(B))')).toEqual([
      t('w ('),
      link('https://nl.wikipedia.org/wiki/A_(B)', 'https://nl.wikipedia.org/wiki/A_(B)'),
      t(')'),
    ]);
  });

  it.each([
    ['javascript', '[x](javascript:alert(1))'],
    ['javascript in capitals', '[x](JaVaScRiPt:alert(1))'],
    ['data', '[x](data:text/html,hi)'],
    ['vbscript', '[x](vbscript:msgbox(1))'],
    ['mailto', '[x](mailto:a@b.nl)'],
    ['ftp', '[x](ftp://files.example)'],
    ['relative', '[x](/admin)'],
    ['relative without a slash', '[x](page.html)'],
    ['protocol-relative', '[x](//evil.example)'],
    ['http without slashes', '[x](http:evil.example)'],
    ['a fragment', '[x](#top)'],
  ])('keeps a %s link as text', (_name, text) => {
    expect(inline(text)).toEqual([t(text)]);
  });

  it('keeps a link with a backslash, a space, an empty or an unclosed address as text', () => {
    expect(inline('[x](https:\\\\evil.example)')).toEqual([t('[x](https:\\evil.example)')]);
    expect(inline('[x]()')).toEqual([t('[x]()')]);
    expect(inline('[x](https://x.nl')).toEqual([t('[x]('), link('https://x.nl/', 'https://x.nl')]);
    expect(inline('[x] (https://x.nl)')).toEqual([t('[x] ('), link('https://x.nl/', 'https://x.nl'), t(')')]);
    expect(inline('[x](https://x.nl and more)')).toEqual([
      t('[x]('),
      link('https://x.nl/', 'https://x.nl'),
      t(' and more)'),
    ]);
  });

  it('reads no link with a bracket in its label', () => {
    expect(inline('[a [b](https://x.nl)')).toEqual([t('[a b ('), link('https://x.nl/', 'https://x.nl'), t(')')]);
    expect(inline('[a \\] b](https://x.nl)')).toEqual([t('a ] b ('), link('https://x.nl/', 'https://x.nl'), t(')')]);
    expect(inline('[a')).toEqual([t('[a')]);
  });

  it('keeps an image as text, and never links it', () => {
    expect(inline('![alt](https://x.nl/a.png) ![x]')).toEqual([t('![alt](https://x.nl/a.png) ![x]')]);
    expect(inline('![alt](javascript:alert(1))')).toEqual([t('![alt](javascript:alert(1))')]);
  });

  it('reads an escaped ! before a link as text and the link as a link', () => {
    expect(inline('\\![x](https://x.nl)')).toEqual([t('!x ('), link('https://x.nl/', 'https://x.nl'), t(')')]);
  });

  it('makes a bare address a link, without the punctuation after it', () => {
    expect(inline('See https://x.nl/a. Or (https://x.nl/b_(c)), https://x.nl/d\'!')).toEqual([
      t('See '),
      link('https://x.nl/a', 'https://x.nl/a'),
      t('. Or ('),
      link('https://x.nl/b_(c)', 'https://x.nl/b_(c)'),
      t('), '),
      link('https://x.nl/d', 'https://x.nl/d'),
      t("'!"),
    ]);
  });

  it('ends a bare address at a space, quote, bracket or angle bracket', () => {
    expect(inline('<https://x.nl> "http://y.nl" [https://z.nl]')).toEqual([
      t('<'),
      link('https://x.nl/', 'https://x.nl'),
      t('> "'),
      link('http://y.nl/', 'http://y.nl'),
      t('" ['),
      link('https://z.nl/', 'https://z.nl'),
      t(']'),
    ]);
  });

  it('reads markers around a bare address as markers', () => {
    expect(inline('**https://x.nl**')).toEqual([strong(link('https://x.nl/', 'https://x.nl'))]);
  });

  it('makes no link of an address glued to a word, or of a scheme alone', () => {
    expect(inline('xhttps://x.nl')).toEqual([t('xhttps://x.nl')]);
    expect(inline('https:// and http://')).toEqual([t('https:// and http://')]);
  });

  it('shows the address as typed, with the address the parser resolves as the target', () => {
    expect(inline('HTTPS://Example.NL:8080/Pad?q=1#x')).toEqual([
      link('https://example.nl:8080/Pad?q=1#x', 'HTTPS://Example.NL:8080/Pad?q=1#x'),
    ]);
  });

  it('shows a host as the browser resolves it, when it is typed in another form', () => {
    // A look-alike: the Cyrillic a in "paypal".
    expect(inline(`https://p${CYRILLIC_A}ypal.com/login`)).toEqual([
      link('https://xn--pypal-4ve.com/login', 'https://xn--pypal-4ve.com/login'),
    ]);
    expect(inline('http://0x7f.1/x')).toEqual([link('http://127.0.0.1/x', 'http://127.0.0.1/x')]);
    expect(inline('https://%65vil.example/')).toEqual([link('https://evil.example/', 'https://evil.example/')]);
    expect(inline('https://x.nl:443/')).toEqual([link('https://x.nl/', 'https://x.nl/')]);
  });

  it.each([
    ['a right-to-left override', `https://evil.example/${RLO}lmth.exe`],
    ['a direction isolate', `https://x.nl/${LRI}a`],
    ['a zero-width space', `https://ex${ZWSP}ample.com`],
    ['a zero-width joiner', `https://x.nl/a${ZWJ}b`],
    ['a soft hyphen', `https://ex${SOFT_HYPHEN}ample.com`],
    ['a Hangul filler', `https://x.nl/${HANGUL_FILLER}`],
    ['a variation selector', `https://x.nl/${VARIATION}`],
    ['a tag variation selector', `https://x.nl/${TAG_VARIATION}`],
  ])('keeps an address with %s as text, bare and in a link', (_name, address) => {
    expect(inline(address)).toEqual([t(address)]);
    expect(inline(`[x](${address})`)).toEqual([t(`[x](${address})`)]);
  });

  it('ends an address at a byte order mark, which counts as a space', () => {
    expect(inline(`https://x.nl/${BOM}x`)).toEqual([link('https://x.nl/', 'https://x.nl/'), t(`${BOM}x`)]);
  });

  it('keeps an address with a user name, an @ or no host as text', () => {
    expect(inline('https://bank.nl@evil.example/')).toEqual([t('https://bank.nl@evil.example/')]);
    expect(inline('[x](https://user:pw@evil.example)')).toEqual([t('[x](https://user:pw@evil.example)')]);
    expect(inline('https:///path')).toEqual([t('https:///path')]);
  });

  it('keeps an address the URL parser refuses as text', () => {
    expect(inline('https://%zz/ and https://a..b:x/')).toEqual([t('https://%zz/ and https://a..b:x/')]);
  });

  it('answers the same through safeLink', () => {
    expect(safeLink('https://x.nl')).toEqual(link('https://x.nl/', 'https://x.nl'));
    expect(safeLink('javascript:alert(1)')).toBeNull();
    expect(safeLink('https://')).toBeNull();
  });
});

describe('renderFeedbackText', () => {
  const NEW_TAB = `rel="${LINK_REL}" dir="ltr" target="_blank"`;

  it('builds every construct as plain elements and text', () => {
    const text = [
      'Hello **world**, *you* and `code`',
      'next line',
      '',
      '- one',
      '  - nested',
      '3. three',
      '',
      '> quoted',
      '',
      '```',
      '<b>&amp;</b>',
      '```',
      '[label](https://x.nl/?a=1&b=<2>)',
      'https://x.nl/?a=1&b=2',
    ].join('\n');
    expect(html(text)).toBe(
      '<p>Hello <strong>world</strong>, <em>you</em> and <code>code</code><br>next line</p>' +
        '<ul><li>one<ul><li>nested</li></ul></li></ul>' +
        '<ol start="3"><li>three</li></ol>' +
        '<blockquote><p>quoted</p></blockquote>' +
        '<pre><code>&lt;b&gt;&amp;amp;&lt;/b&gt;</code></pre>' +
        '<p>[label](<a href="https://x.nl/?a=1&amp;b=" ' +
        `${NEW_TAB}>https://x.nl/?a=1&amp;b=</a>&lt;2&gt;)<br>` +
        `<a href="https://x.nl/?a=1&amp;b=2" ${NEW_TAB}>https://x.nl/?a=1&amp;b=2</a></p>`,
    );
  });

  it('gives a numbered list from 1 no start', () => {
    expect(html('1. a')).toBe('<ol><li>a</li></ol>');
  });

  it('adds the words for a new tab, visually hidden, inside the link', () => {
    expect(html('https://x.nl', { newTabHint: '(opens in a new tab)' })).toBe(
      `<p><a href="https://x.nl/" ${NEW_TAB}>https://x.nl<span class="${HIDDEN_CLASS}"> (opens in a new tab)</span></a></p>`,
    );
  });

  it('opens links in the same tab when asked, as on an opaque origin, without the words for a new tab', () => {
    expect(html('https://x.nl', { newTab: false, newTabHint: '(opens in a new tab)' })).toBe(
      `<p><a href="https://x.nl/" rel="${LINK_REL}" dir="ltr">https://x.nl</a></p>`,
    );
  });

  it('builds in the document it is given', () => {
    const other = document.implementation.createHTMLDocument('other');
    const fragment = renderFeedbackText('**a** https://x.nl\n\n- b', other);
    const nodes = [...fragment.querySelectorAll('*')];
    expect(nodes.length).toBeGreaterThan(0);
    expect(nodes.every((node) => node.ownerDocument === other)).toBe(true);
  });

  it('builds an empty fragment from an empty text', () => {
    expect(renderFeedbackText('', document).childNodes).toHaveLength(0);
  });

  it('makes nothing but the plain elements of its subset from hostile input', () => {
    const hostile = [
      '<img src=x onerror=alert(1)> <script>alert(1)</script> <iframe src="https://x.nl"></iframe>',
      '<a href="javascript:alert(1)">x</a> <svg onload=alert(1)> <style>*{}</style> <link rel=stylesheet href=x>',
      '![x](https://x.nl/a.png) [x](javascript:alert(1)) [x](data:text/html,<script>alert(1)</script>)',
      '<object data=x> <embed src=x> <video src=x> <audio src=x> <base href=x> <meta http-equiv=refresh>',
      '&lt;script&gt; javascript:alert(1) "><img src=x>',
      '> <img src=x>\n- <script>\n```\n<img src=x>\n```',
      '`<img src=x>` **<b>** _<i>_ [<img src=x>](https://x.nl) https://x.nl/<img>',
    ].join('\n\n');
    const box = document.createElement('div');
    box.appendChild(renderFeedbackText(hostile, document, { newTabHint: 'new tab' }));
    const tags = new Set([...box.querySelectorAll('*')].map((node) => node.tagName.toLowerCase()));
    const allowed = ['p', 'br', 'ul', 'ol', 'li', 'pre', 'code', 'blockquote', 'strong', 'em', 'a', 'span'];
    expect([...tags].filter((tag) => !allowed.includes(tag))).toEqual([]);
    const attributes = new Set([...box.querySelectorAll('*')].flatMap((node) => node.getAttributeNames()));
    expect([...attributes].sort()).toEqual(['class', 'dir', 'href', 'rel', 'target']);
    for (const anchor of box.querySelectorAll('a')) {
      expect(anchor.getAttribute('href')).toMatch(/^https:\/\/x\.nl\//);
      expect(anchor.textContent).toMatch(/^https:\/\/x\.nl/);
    }
    expect(box.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(box.textContent).toContain('[x](javascript:alert(1))');
  });
});

describe('feedbackPlainText', () => {
  it('drops the markup and keeps every word, a link as its address', () => {
    const text = '**Bold** and `code`\nnext\n\n- a\n  - b\n- [c](https://x.nl)\n\n> q\n>\n> r\n\n```\nx\n```';
    expect(feedbackPlainText(text)).toBe('Bold and code\nnext\n\na\nb\nc (https://x.nl)\n\nq\n\nr\n\nx');
  });

  it('is empty for an empty text', () => {
    expect(feedbackPlainText('')).toBe('');
  });
});

describe('the cost of pathological input', () => {
  const MAX = 5000;
  const cases: [string, string][] = [
    ['10k backticks', '`'.repeat(10_000)],
    ['backticks of every length', Array.from({ length: 100 }, (_, n) => `${'`'.repeat(n + 1)}a`).join('')],
    ['backtick pairs that never close', '`a``'.repeat(MAX / 4)],
    ['5000 nested >', '>'.repeat(MAX)],
    ['nested > with spaces', '> '.repeat(MAX / 2)],
    ['5000 lines of >', '>\n'.repeat(MAX / 2)],
    ['unbalanced **', '**a '.repeat(MAX / 4)],
    ['unbalanced _', '_a '.repeat(MAX / 3)],
    ['_ inside words', 'a_'.repeat(MAX / 2)],
    ['alternating *_', '*_'.repeat(MAX / 2)],
    ['one run of stars', '*'.repeat(MAX)],
    ['openers then closers', `${'*a '.repeat(MAX / 6)}${' a*'.repeat(MAX / 6)}`],
    ['mixed openers', '*a **b _c __d '.repeat(MAX / 15)],
    ['brackets', '['.repeat(MAX)],
    ['labels without a link', '[a]'.repeat(MAX / 3)],
    ['links that never close', '[a](b'.repeat(MAX / 5)],
    ['parentheses that never close', `[a](${'('.repeat(MAX)}`],
    ['images', '![a](https://x.nl/'.repeat(MAX / 18)],
    ['schemes', 'https://'.repeat(MAX / 8)],
    ['addresses in parentheses', '(https://x.nl/'.repeat(MAX / 14)],
    ['closing parentheses after an address', `https://x.nl/${')'.repeat(MAX)}`],
    ['trailing punctuation after an address', `https://x.nl/${'.'.repeat(MAX)}`],
    ['backslashes', '\\'.repeat(MAX)],
    ['escapes', '\\*'.repeat(MAX / 2)],
    ['nested items', '  - a\n'.repeat(MAX / 6)],
    ['numbered items', '1. a\n'.repeat(MAX / 5)],
    ['fences', '```\n'.repeat(MAX / 4)],
    ['blank lines', ' \n'.repeat(MAX / 2)],
    ['line breaks', 'a\n'.repeat(MAX / 2)],
  ];

  it.each(cases)('handles %s in well under 100 ms', (_name, text) => {
    const started = performance.now();
    renderFeedbackText(text, document);
    expect(performance.now() - started).toBeLessThan(100);
  });
});

describe('the sources', () => {
  /** The source without comments, so prose about a rule is no finding. */
  function code(file: string): string {
    let source = readFileSync(file, 'utf8');
    for (let previous = ''; previous !== source; ) {
      previous = source;
      source = source
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/<!--[\s\S]*?-->/g, '')
        .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
    }
    return source;
  }

  const files = [join(HERE, 'markdown.ts'), join(HERE, 'styles.ts')];

  it.each([
    ['innerHTML', /\binnerHTML\b/],
    ['outerHTML', /\bouterHTML\b/],
    ['insertAdjacentHTML', /\binsertAdjacentHTML\b/],
    ['v-html', /\bv-html\b/],
    ['DOMParser', /\bDOMParser\b/],
    ['createContextualFragment', /\bcreateContextualFragment\b/],
    ['document.write', /\.\s*write(?:ln)?\s*\(/],
    ['srcdoc', /\bsrcdoc\b/],
    [
      'an element that loads something',
      // As the argument of a call: `createElement('img')`, `element(doc, 'img')`, `h('img')`.
      /[(,]\s*['"](?:img|image|picture|source|video|audio|iframe|frame|object|embed|script|link|style|svg|base|meta|form|input)['"]/,
    ],
    ['a coverage exclusion', /v8 ignore/],
  ])('never use %s', (_name, pattern) => {
    const offending = files.filter((file) => pattern.test(code(file)));
    expect(offending).toEqual([]);
  });

  it('reads both files', () => {
    for (const file of files) expect(code(file).length).toBeGreaterThan(100);
  });
});

describe('the widget styles', () => {
  it('hang off the element the widget puts the text in', () => {
    const rules = FEEDBACK_TEXT_STYLES.split('\n').filter((line) => line.includes('{') && !line.startsWith('@'));
    expect(rules.length).toBeGreaterThan(10);
    for (const rule of rules) expect(rule.trim().startsWith(`.${TEXT_CLASS}`)).toBe(true);
    expect(FEEDBACK_TEXT_STYLES).toContain(`.${TEXT_CLASS} .${HIDDEN_CLASS} {`);
  });

  it('take their colours from the widget tokens only, so they follow light and dark', () => {
    for (const token of ['--tf-text', '--tf-accent', '--tf-tinted', '--tf-divider', '--tf-radius-small']) {
      expect(FEEDBACK_TEXT_STYLES).toContain(`var(${token})`);
    }
    expect(FEEDBACK_TEXT_STYLES).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|oklch\(/i);
  });

  it('load nothing', () => {
    expect(FEEDBACK_TEXT_STYLES).not.toMatch(/url\(|@import|@font-face/);
  });

  it('give no fixed width, and let code scroll inside its block', () => {
    const widths = [...FEEDBACK_TEXT_STYLES.matchAll(/(?:^|[\s;{])(?:max-|min-)?width:\s*([^;]+);/g)].map((match) => match[1]);
    expect(widths.sort()).toEqual(['100%', '1px']);
    expect(FEEDBACK_TEXT_STYLES).toMatch(/pre \{[^}]*max-width: 100%;[^}]*overflow-x: auto;/);
    expect(FEEDBACK_TEXT_STYLES).toContain('font-size: 16px;');
    expect(FEEDBACK_TEXT_STYLES).toContain('monospace');
  });
});
