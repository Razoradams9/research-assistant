/**
 * Tiny, dependency-free Markdown -> HTML renderer.
 *
 * Deliberately minimal: it covers exactly what the Synthesizer agent
 * emits — headings, bold/italic, inline code, links, unordered/ordered
 * lists, horizontal rules, and paragraphs. No CDN, works offline.
 *
 * SECURITY: all input is HTML-escaped BEFORE any markdown tokens are
 * turned into tags, so model-generated text can never inject markup.
 */
(function (global) {
  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function inline(text) {
    // Inline code first (protect its contents from other rules).
    let out = text.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
    // Links [text](url) — only http(s) URLs are allowed.
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, url) => {
      return `<a href="${url}" target="_blank" rel="noopener noreferrer">${t}</a>`;
    });
    // Bold then italic.
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    return out;
  }

  function render(md) {
    const src = escapeHtml(md || "").replace(/\r\n/g, "\n");
    const lines = src.split("\n");
    const html = [];
    let listType = null; // 'ul' | 'ol' | null
    let paragraph = [];

    function flushParagraph() {
      if (paragraph.length) {
        html.push(`<p>${inline(paragraph.join(" "))}</p>`);
        paragraph = [];
      }
    }
    function closeList() {
      if (listType) {
        html.push(`</${listType}>`);
        listType = null;
      }
    }

    for (const raw of lines) {
      const line = raw.trimEnd();

      if (!line.trim()) {
        flushParagraph();
        closeList();
        continue;
      }

      // Headings
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        flushParagraph();
        closeList();
        const level = h[1].length;
        html.push(`<h${level}>${inline(h[2])}</h${level}>`);
        continue;
      }

      // Horizontal rule
      if (/^(---|\*\*\*|___)\s*$/.test(line)) {
        flushParagraph();
        closeList();
        html.push("<hr />");
        continue;
      }

      // Ordered list item: "1. text"
      const ol = line.match(/^\s*\d+\.\s+(.*)$/);
      if (ol) {
        flushParagraph();
        if (listType !== "ol") { closeList(); html.push("<ol>"); listType = "ol"; }
        html.push(`<li>${inline(ol[1])}</li>`);
        continue;
      }

      // Unordered list item: "- text" or "* text"
      const ul = line.match(/^\s*[-*]\s+(.*)$/);
      if (ul) {
        flushParagraph();
        if (listType !== "ul") { closeList(); html.push("<ul>"); listType = "ul"; }
        html.push(`<li>${inline(ul[1])}</li>`);
        continue;
      }

      // Otherwise accumulate into a paragraph.
      closeList();
      paragraph.push(line.trim());
    }

    flushParagraph();
    closeList();
    return html.join("\n");
  }

  global.miniMarkdown = { render };
})(window);
