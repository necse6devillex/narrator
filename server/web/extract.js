(function () {
  const scriptCache = {};
  function loadScript(src) {
    if (!scriptCache[src]) {
      scriptCache[src] = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = src;
        s.onload = resolve;
        s.onerror = () => { delete scriptCache[src]; reject(new Error('Could not load ' + src + ' — open the app once while online.')); };
        document.head.appendChild(s);
      });
    }
    return scriptCache[src];
  }

  function normalizeText(raw) {
    let t = raw.replace(/\r/g, '').replace(/ /g, ' ');
    t = t.replace(/(\w)-\n(?=[a-z])/g, '$1');
    t = t.replace(/([^\n.!?])\n(?=[a-z0-9(\[,;])/g, '$1 ');
    t = t.replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n');
    return t.trim();
  }

  const BLOCK = new Set(['P', 'DIV', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'LI', 'BR', 'TR', 'BLOCKQUOTE', 'SECTION', 'ARTICLE', 'PRE']);
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'NAV', 'SVG', 'HEAD']);

  function domToText(node) {
    let out = '';
    (function walk(n) {
      if (n.nodeType === 3) { out += n.nodeValue; return; }
      if (n.nodeType !== 1) return;
      const tag = n.tagName.toUpperCase();
      if (SKIP.has(tag)) return;
      const isBlock = BLOCK.has(tag);
      if (isBlock) out += '\n';
      for (const c of n.childNodes) walk(c);
      if (isBlock) out += '\n';
    })(node);
    return out.replace(/[ \t]*\n[ \t]*/g, '\n').replace(/\n{2,}/g, '\n\n');
  }

  function htmlToText(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return domToText(doc.body || doc.documentElement);
  }

  async function readPdf(file, onProgress) {
    await loadScript('/vendor/pdf.min.js');
    pdfjsLib.GlobalWorkerOptions.workerSrc = '/vendor/pdf.worker.min.js';
    const data = new Uint8Array(await file.arrayBuffer());
    const doc = await pdfjsLib.getDocument({ data }).promise;
    const pages = [];
    for (let p = 1; p <= doc.numPages; p++) {
      onProgress('Reading page ' + p + ' of ' + doc.numPages + '…');
      const page = await doc.getPage(p);
      const content = await page.getTextContent();
      let text = '';
      let lastY = null;
      for (const item of content.items) {
        if (typeof item.str !== 'string') continue;
        const y = item.transform ? item.transform[5] : null;
        if (lastY !== null && y !== null) {
          const gap = Math.abs(y - lastY);
          const h = item.height || 10;
          if (gap > h * 1.9) text = text.replace(/\n*$/, '\n\n');
          else if (gap > 0.5 && !text.endsWith('\n')) text += '\n';
        }
        text += item.str;
        if (item.hasEOL) text += '\n';
        if (y !== null) lastY = y;
      }
      pages.push(text.split('\n').filter(l => !/^\s*\d{1,4}\s*$/.test(l)).join('\n'));
    }
    return pages.join('\n\n');
  }

  async function readDocx(file) {
    await loadScript('/vendor/mammoth.browser.min.js');
    const result = await mammoth.extractRawText({ arrayBuffer: await file.arrayBuffer() });
    return result.value;
  }

  async function readEpub(file, onProgress) {
    await loadScript('/vendor/jszip.min.js');
    const zip = await JSZip.loadAsync(await file.arrayBuffer());
    const xml = (s) => new DOMParser().parseFromString(s, 'application/xml');
    const container = xml(await zip.file('META-INF/container.xml').async('string'));
    const opfPath = container.querySelector('rootfile').getAttribute('full-path');
    const opf = xml(await zip.file(opfPath).async('string'));
    const base = opfPath.includes('/') ? opfPath.slice(0, opfPath.lastIndexOf('/') + 1) : '';
    const manifest = {};
    opf.querySelectorAll('manifest > item').forEach(it => {
      manifest[it.getAttribute('id')] = {
        href: it.getAttribute('href'),
        type: it.getAttribute('media-type') || '',
        props: it.getAttribute('properties') || '',
      };
    });
    const spine = [...opf.querySelectorAll('spine > itemref')].map(r => manifest[r.getAttribute('idref')]).filter(Boolean);
    const parts = [];
    let n = 0;
    for (const item of spine) {
      n++;
      if (!/html/.test(item.type) || /\bnav\b/.test(item.props)) continue;
      onProgress('Reading section ' + n + ' of ' + spine.length + '…');
      const path = decodeURIComponent(base + item.href);
      const entry = zip.file(path);
      if (!entry) continue;
      parts.push(htmlToText(await entry.async('string')));
    }
    const titleEl = opf.querySelector('metadata > title, metadata title');
    return { text: parts.join('\n\n'), title: titleEl ? titleEl.textContent.trim() : '' };
  }

  function stripMarkdown(t) {
    return t
      .replace(/```[\s\S]*?```/g, '')
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/^\s{0,3}#{1,6}\s*/gm, '')
      .replace(/^\s*[-*+]\s+/gm, '')
      .replace(/^\s*>\s?/gm, '');
  }

  window.extractTextFromFile = async function (file, onProgress) {
    onProgress = onProgress || function () {};
    const name = file.name || 'Untitled';
    const ext = (name.split('.').pop() || '').toLowerCase();
    let title = name.replace(/\.[^.]+$/, '');
    let text;
    if (ext === 'pdf') {
      text = await readPdf(file, onProgress);
    } else if (ext === 'docx') {
      text = await readDocx(file);
    } else if (ext === 'epub') {
      const r = await readEpub(file, onProgress);
      text = r.text;
      if (r.title) title = r.title;
    } else if (ext === 'html' || ext === 'htm') {
      text = htmlToText(await file.text());
    } else if (ext === 'md' || ext === 'markdown') {
      text = stripMarkdown(await file.text());
    } else if (ext === 'txt' || (file.type || '').startsWith('text/')) {
      text = await file.text();
    } else {
      throw new Error('Unsupported file type ".' + ext + '". Try PDF, EPUB, DOCX, HTML, TXT or Markdown.');
    }
    text = normalizeText(text);
    if (!text) {
      throw new Error(ext === 'pdf'
        ? 'No readable text found — this PDF may be scanned pages (images), which can\'t be read aloud without OCR.'
        : 'No readable text found in this file.');
    }
    return { title, text };
  };
})();
