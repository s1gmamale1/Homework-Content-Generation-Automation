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

/* ─── Premium interactions ─────────────────────────────────────── */

const reduceMotion = () =>
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Track mouse position over an element and write CSS variables --mx/--my
 * (as percentages) so CSS gradients can spotlight where the cursor is.
 * Adds a subtle 3D tilt as a side effect.
 */
function attachInteractive(element, options = {}) {
    if (reduceMotion()) return;
    const { tilt = true, tiltMax = 4 } = options;
    let raf = 0;

    const onMove = (e) => {
        const rect = element.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
            element.style.setProperty('--mx', `${(x * 100).toFixed(2)}%`);
            element.style.setProperty('--my', `${(y * 100).toFixed(2)}%`);
            if (tilt) {
                const rx = (0.5 - y) * tiltMax;
                const ry = (x - 0.5) * tiltMax;
                element.style.transform =
                    `perspective(900px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg)`;
            }
        });
    };

    const onLeave = () => {
        cancelAnimationFrame(raf);
        if (tilt) element.style.transform = '';
        element.style.removeProperty('--mx');
        element.style.removeProperty('--my');
    };

    element.addEventListener('mousemove', onMove);
    element.addEventListener('mouseleave', onLeave);
}

/**
 * Magnetic hover — element follows the cursor slightly while inside.
 */
function attachMagnet(element, strength = 0.22) {
    if (reduceMotion()) return;
    let raf = 0;
    const onMove = (e) => {
        const rect = element.getBoundingClientRect();
        const dx = (e.clientX - (rect.left + rect.width / 2)) * strength;
        const dy = (e.clientY - (rect.top + rect.height / 2)) * strength;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
            element.style.transform = `translate(${dx.toFixed(2)}px, ${dy.toFixed(2)}px)`;
        });
    };
    const onLeave = () => {
        cancelAnimationFrame(raf);
        element.style.transform = '';
    };
    element.addEventListener('mousemove', onMove);
    element.addEventListener('mouseleave', onLeave);
}

/**
 * Inject the aurora background once (idempotent).
 * Pages that want the animated backdrop can call window.installAurora() once.
 */
function installAurora() {
    if (document.querySelector('.aurora')) return;
    const aurora = document.createElement('div');
    aurora.className = 'aurora';
    aurora.setAttribute('aria-hidden', 'true');
    for (let i = 1; i <= 3; i++) {
        const orb = document.createElement('div');
        orb.className = `orb orb-${i}`;
        aurora.appendChild(orb);
    }
    document.body.insertBefore(aurora, document.body.firstChild);
}

/* Auto-install aurora on every page that loads this script */
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installAurora);
} else {
    installAurora();
}

window.SUBJECTS = SUBJECTS;
window.uploadBook = uploadBook;
window.generate = generate;
window.streamSse = streamSse;
window.el = el;
window.clear = clear;
window.attachInteractive = attachInteractive;
window.attachMagnet = attachMagnet;
window.installAurora = installAurora;
