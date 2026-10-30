// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/**
 * A small Markdown renderer for the device knowledge base on the website (#512), without a dependency. It knows what
 * the pages in docs/devices/ use, as GitHub shows it: headings (with GitHub's ids, so "page.md#heading" links keep
 * working), paragraphs, block quotes, lists (nested, ordered), tables, fenced code, rules, hard line breaks, and inline
 * code, links, https:// addresses, <https://…>, **strong**, *em*, _em_, ~~strike~~ and backslash escapes.
 *
 * An HTML comment on a line of its own is left out, as GitHub hides it.
 *
 * Safe by construction: every text is escaped, raw HTML in the Markdown is shown as text, and an address becomes a
 * link only if options.link returns one for it (else the link text stays as plain text). Usage:
 *   renderMarkdown(source, { link: (href) => href or null }) → { html, title, ids }
 * title is the text of the first level-1 heading, ids the ids of all headings.
 */

const escapeHtml = (value) => value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const ENTITIES = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"' };

/** The plain text of inline HTML rendered here, for the id and the title of a heading: the tags left out, the entities
 *  decoded. Never HTML again: the title is escaped where it is used. */
function textOf(html) {
  let text = "";
  for (let i = 0; i < html.length; i++) {
    if (html[i] === "<") {
      // only the tags of this renderer: a "<" of the text is &lt;
      const end = html.indexOf(">", i);
      if (end < 0) break;
      i = end;
    } else if (html[i] === "&") {
      const entity = Object.keys(ENTITIES).find((name) => html.startsWith(name, i));
      text += entity ? ENTITIES[entity] : "&";
      if (entity) i += entity.length - 1;
    } else text += html[i];
  }
  return text.trim();
}

// a token stands for already rendered HTML while the emphasis is matched on the escaped text around it
const TOKEN = /\u0000(\d+)\u0000/g;
const PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";

/** Renders one line or paragraph of inline Markdown to HTML. */
function inline(text, options) {
  const tokens = [];
  const token = (html) => `\u0000${tokens.push(html) - 1}\u0000`;
  let out = "";
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "\\" && i + 1 < text.length && PUNCTUATION.includes(text[i + 1])) {
      out += token(escapeHtml(text[i + 1]));
      i += 2;
      continue;
    }
    if (c === "\\" && text[i + 1] === "\n") {
      out += token("<br />\n");
      i += 2;
      continue;
    }
    if (c === "`") {
      const run = /^`+/.exec(text.slice(i))[0];
      const end = text.indexOf(run, i + run.length);
      // the closing run must have the same length, not be part of a longer one
      let close = end;
      while (close >= 0 && (text[close + run.length] === "`" || text[close - 1] === "`")) close = text.indexOf(run, close + 1);
      if (close < 0) {
        out += escapeHtml(run);
        i += run.length;
        continue;
      }
      let code = text.slice(i + run.length, close).replace(/\n/g, " ");
      if (/^ .*[^ ].* $/.test(code)) code = code.slice(1, -1);
      out += token(`<code>${escapeHtml(code)}</code>`);
      i = close + run.length;
      continue;
    }
    if (c === "<") {
      const auto = /^<(https:\/\/[^\s<>]+)>/.exec(text.slice(i));
      if (auto) {
        out += token(linkHtml(escapeHtml(auto[1]), auto[1], options));
        i += auto[0].length;
        continue;
      }
    }
    if (c === "[" || (c === "!" && text[i + 1] === "[")) {
      const link = parseLink(text, c === "!" ? i + 1 : i);
      if (link) {
        out += token(linkHtml(inline(link.label, { ...options, inLink: true }), link.href, options));
        i = link.end;
        continue;
      }
    }
    if (c === "h" && !options.inLink && /^https:\/\//.test(text.slice(i, i + 8)) && !/[\p{L}\p{N}]/u.test(text[i - 1] ?? "")) {
      let address = /^https:\/\/[^\s<>]+/.exec(text.slice(i))[0];
      // GitHub leaves trailing punctuation and an unbalanced ")" out of the link
      for (;;) {
        if (/[.,:;!?*_~'"]$/.test(address)) address = address.slice(0, -1);
        else if (address.endsWith(")") && address.split("(").length < address.split(")").length) address = address.slice(0, -1);
        else break;
      }
      out += token(linkHtml(escapeHtml(address), address, options));
      i += address.length;
      continue;
    }
    out += escapeHtml(c);
    i += 1;
  }
  // a line break in a paragraph after two spaces
  out = out.replace(/ {2,}\n/g, () => token("<br />\n"));
  out = out
    .replace(/\*\*(\S(?:[\s\S]*?\S)?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<![\p{L}\p{N}_])__(\S(?:[\s\S]*?\S)?)__(?![\p{L}\p{N}_])/gu, "<strong>$1</strong>")
    .replace(/\*([^\s*](?:[\s\S]*?[^\s*])?)\*/g, "<em>$1</em>")
    .replace(/(?<![\p{L}\p{N}_])_([^\s_](?:[\s\S]*?[^\s_])?)_(?![\p{L}\p{N}_])/gu, "<em>$1</em>")
    .replace(/~~(\S(?:[\s\S]*?\S)?)~~/g, "<del>$1</del>");
  // tokens can contain tokens (the label of a link)
  while (TOKEN.test(out)) out = out.replace(TOKEN, (_, n) => tokens[Number(n)]);
  return out;
}

/** [label](href "title") starting at text[start] = "[": {label, href, end} or null. */
function parseLink(text, start) {
  let depth = 0;
  let i = start;
  for (; i < text.length; i++) {
    if (text[i] === "\\") i += 1;
    else if (text[i] === "`") {
      const run = /^`+/.exec(text.slice(i))[0];
      const close = text.indexOf(run, i + run.length);
      if (close >= 0) i = close + run.length - 1;
    } else if (text[i] === "[") depth += 1;
    else if (text[i] === "]" && --depth === 0) break;
  }
  if (depth !== 0 || text[i + 1] !== "(") return null;
  const target = /^\(\s*(<[^<>\n]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)(?:\s+"[^"]*"|\s+'[^']*')?\s*\)/.exec(text.slice(i + 1));
  if (!target) return null;
  const href = target[1].replace(/^<(.*)>$/, "$1").replace(/\\([!-/:-@[-`{-~])/g, "$1");
  return { label: text.slice(start + 1, i), href, end: i + 1 + target[0].length };
}

function linkHtml(labelHtml, href, options) {
  if (options.inLink) return labelHtml;
  const target = options.link ? options.link(href) : null;
  return target ? `<a href="${escapeHtml(target)}">${labelHtml}</a>` : labelHtml;
}

/** GitHub's id of a heading: lower case, without punctuation, spaces as "-"; repeated ones get -1, -2 … */
function slug(html, ids) {
  const text = textOf(html).toLowerCase();
  const base = text.replace(/[^\p{L}\p{M}\p{N}\s_-]/gu, "").replace(/\s/g, "-");
  let id = base;
  for (let n = 1; ids.includes(id); n++) id = `${base}-${n}`;
  ids.push(id);
  return id;
}

const indentOf = (line) => /^ */.exec(line)[0].length;
const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const HEADING = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const QUOTE = /^ {0,3}> ?/;
const ITEM = /^( {0,3})([-*+]|\d{1,9}[.)])( +|$)/;
const DELIMITER = /^ {0,3}\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;

/** The cells of a table row; "\|" is a "|" inside a cell. */
function cells(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const result = [];
  let cell = "";
  for (let i = 0; i < row.length; i++) {
    if (row[i] === "\\" && row[i + 1] === "|") {
      cell += "|";
      i += 1;
    } else if (row[i] === "|") {
      result.push(cell.trim());
      cell = "";
    } else cell += row[i];
  }
  result.push(cell.trim());
  return result;
}

const isTableStart = (lines, i) => lines[i].includes("|") && i + 1 < lines.length && DELIMITER.test(lines[i + 1])
  && lines[i + 1].includes("-") && cells(lines[i]).length === cells(lines[i + 1]).length;
/** Whether line starts a list item (a marker with text after it). Inside a paragraph only a bullet or "1." does, so a
 *  line such as "2026. …" continues the paragraph. */
function isItem(line, inParagraph = false) {
  const item = ITEM.exec(line);
  return !!item && line.slice(item[0].length).trim() !== "" && (!inParagraph || !/\d/.test(item[2]) || Number.parseInt(item[2], 10) === 1);
}
const startsBlock = (lines, i) => FENCE.test(lines[i]) || HEADING.test(lines[i]) || RULE.test(lines[i]) || QUOTE.test(lines[i])
  || isItem(lines[i], true) || isTableStart(lines, i);

/** Renders lines of block Markdown; tight: paragraphs without <p> (the items of a list without blank lines). */
function blocks(lines, options, tight = false) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // blank lines, and HTML comments on a line of their own (the markers of generated parts, #526), as on GitHub
    if (!line.trim() || (line.trim().startsWith("<!--") && line.trim().endsWith("-->"))) {
      i += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const indent = indentOf(line);
      const body = [];
      for (i += 1; i < lines.length; i++) {
        const close = FENCE.exec(lines[i]);
        if (close && close[1][0] === fence[1][0] && close[1].length >= fence[1].length && !close[2].trim()) {
          i += 1;
          break;
        }
        body.push(lines[i].slice(Math.min(indent, indentOf(lines[i]))));
      }
      out.push(`<pre><code>${escapeHtml(body.join("\n"))}${body.length ? "\n" : ""}</code></pre>`);
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      const level = heading[1].length;
      const html = inline(heading[2] ?? "", options);
      const id = slug(html, options.ids);
      if (level === 1 && options.title === undefined) options.title = textOf(html);
      out.push(`<h${level} id="${escapeHtml(id)}">${html}</h${level}>`);
      i += 1;
      continue;
    }
    if (RULE.test(line)) {
      out.push("<hr />");
      i += 1;
      continue;
    }
    if (QUOTE.test(line)) {
      const body = [];
      while (i < lines.length && lines[i].trim() && (QUOTE.test(lines[i]) || !startsBlock(lines, i))) {
        body.push(lines[i].replace(QUOTE, ""));
        i += 1;
      }
      out.push(`<blockquote>\n${blocks(body, options)}\n</blockquote>`);
      continue;
    }
    if (isTableStart(lines, i)) {
      const align = cells(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : ""));
      const row = (line, tag) => {
        const values = cells(line);
        return `<tr>${align.map((a, n) => `<${tag}${a ? ` class="${a}"` : ""}>${inline(values[n] ?? "", options)}</${tag}>`).join("")}</tr>`;
      };
      const head = row(lines[i], "th");
      const body = [];
      for (i += 2; i < lines.length && lines[i].trim() && lines[i].includes("|") && !startsBlock(lines, i); i++) body.push(row(lines[i], "td"));
      out.push(`<div class="table"><table>\n<thead>${head}</thead>\n${body.length ? `<tbody>\n${body.join("\n")}\n</tbody>\n` : ""}</table></div>`);
      continue;
    }
    const item = ITEM.exec(line);
    if (isItem(line)) {
      const ordered = /\d/.test(item[2]);
      const marker = item[2].slice(-1);
      const start = ordered ? Number.parseInt(item[2], 10) : 1;
      const items = [];
      let loose = false;
      while (i < lines.length) {
        const current = ITEM.exec(lines[i]);
        if (!isItem(lines[i]) || /\d/.test(current[2]) !== ordered || current[2].slice(-1) !== marker) break;
        const content = current[0].length;
        const body = [lines[i].slice(content)];
        for (i += 1; i < lines.length; i++) {
          if (!lines[i].trim()) {
            // a blank line ends the item unless the next line is indented to its content
            const next = lines.slice(i).findIndex((l) => l.trim());
            if (next < 0 || indentOf(lines[i + next]) < content) break;
            body.push("");
            continue;
          }
          if (indentOf(lines[i]) >= content) body.push(lines[i].slice(content));
          else if (body[body.length - 1].trim() && !startsBlock(lines, i) && !isItem(lines[i])) body.push(lines[i].trim()); // lazy continuation
          else break;
        }
        if (body.slice(0, -1).some((l) => !l.trim())) loose = true;
        items.push(body);
        if (i < lines.length && !lines[i].trim()) {
          const next = lines.slice(i).findIndex((l) => l.trim());
          const sibling = next >= 0 && isItem(lines[i + next]) ? ITEM.exec(lines[i + next]) : null;
          if (sibling && indentOf(lines[i + next]) === indentOf(line) && /\d/.test(sibling[2]) === ordered && sibling[2].slice(-1) === marker) {
            loose = true;
            i += next;
          }
        }
      }
      const tag = ordered ? "ol" : "ul";
      const open = ordered && start !== 1 ? `<ol start="${start}">` : `<${tag}>`;
      out.push(`${open}\n${items.map((body) => `<li>${blocks(body, options, !loose)}</li>`).join("\n")}\n</${tag}>`);
      continue;
    }
    const paragraph = [];
    while (i < lines.length && lines[i].trim() && (paragraph.length === 0 || !startsBlock(lines, i))) {
      paragraph.push(lines[i].replace(/^ +/, ""));
      i += 1;
    }
    const html = inline(paragraph.join("\n").replace(/[ \t]+$/, ""), options);
    out.push(tight ? html : `<p>${html}</p>`);
  }
  return out.join("\n");
}

export function renderMarkdown(source, { link } = {}) {
  // no NUL in the text, it marks the tokens; tabs as four spaces for the indentation of lists and code
  const lines = source.replace(/\0/g, "�").replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");
  const options = { link, ids: [], title: undefined };
  const html = blocks(lines, options);
  return { html, title: options.title, ids: options.ids };
}
