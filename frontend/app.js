const SUBJECTS = [
    "biology", "english", "geometriya-g7-11", "history",
    "kimyo-g7-11", "math-algebra", "physics",
];

async function uploadBook(file, subject) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject);
    const res = await fetch("/api/v1/books", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`upload failed: ${res.status} ${await res.text()}`);
    return await res.json();
}

async function generate(bookId, sectionId) {
    const res = await fetch(
        `/api/v1/books/${bookId}/sections/${sectionId}/generate`,
        { method: "POST", headers: {"Content-Type": "application/json"}, body: "{}" }
    );
    if (!res.ok) throw new Error(`generate failed: ${res.status} ${await res.text()}`);
    return await res.json();
}

function streamSse(url, handlers) {
    const es = new EventSource(url);
    for (const [name, fn] of Object.entries(handlers)) {
        es.addEventListener(name, (e) => fn(JSON.parse(e.data)));
    }
    es.onerror = () => es.close();
    return es;
}

// Safe DOM helpers — never use innerHTML with server data
function el(tag, opts = {}) {
    const node = document.createElement(tag);
    if (opts.className) node.className = opts.className;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.href != null) node.href = opts.href;
    if (opts.attrs) {
        for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
    }
    return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

window.SUBJECTS = SUBJECTS;
window.uploadBook = uploadBook;
window.generate = generate;
window.streamSse = streamSse;
window.el = el;
window.clear = clear;
