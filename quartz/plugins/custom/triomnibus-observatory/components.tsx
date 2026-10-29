import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "../../../components/types"
import { pathToRoot, joinSegments } from "../../../util/path"

export const TriomnibusObservatory: QuartzComponentConstructor = () => {
  const Component: QuartzComponent = ({ fileData }: QuartzComponentProps) => {
    const slug = fileData.slug ?? "index"
    const baseDir = pathToRoot(slug)
    const obsUrl = `${joinSegments(baseDir, "static/observatory/index.html")}?focus=${encodeURIComponent(slug)}`
    const graphJsonUrl = joinSegments(baseDir, "static/triomnibus-graph.json")

    return (
      <div class="triomnibus-graph-card" data-slug={slug} data-graph-url={graphJsonUrl} data-obs-url={obsUrl}>
        <div class="tgc-header">
          <h3>Constellation</h3>
          <div class="tgc-actions">
            <button type="button" class="tgc-modal-btn" aria-label="Expand Graph Observatory">
              ⤢ Observatory
            </button>
            <a class="tgc-open-link" href={obsUrl} title="Open standalone Graph Observatory">
              ↗
            </a>
          </div>
        </div>
        <div class="tgc-preview-wrap">
          <canvas class="tgc-mini-canvas" width="280" height="210"></canvas>
        </div>
        <div class="tgc-caption">
          Typed neighborhood (apparatus hubs excluded) · Click <strong>⤢ Observatory</strong> for Moretti ablation, <em>Chavruta</em> paths &amp; Oracle Q&amp;A.
        </div>

        <dialog class="tgc-dialog" aria-label="Triomnibus Graph Observatory">
          <div class="tgc-dialog-bar">
            <span>The Hypertext Triomnibus — Graph Observatory</span>
            <button type="button" class="tgc-dialog-close" aria-label="Close Graph Observatory">✕ Close</button>
          </div>
          <iframe class="tgc-iframe" title="Triomnibus Graph Observatory" loading="lazy"></iframe>
        </dialog>
      </div>
    )
  }

  Component.css = `
  .triomnibus-graph-card {
    margin: 0.4rem 0 1.2rem;
    border: 1px solid var(--lightgray);
    border-radius: 6px;
    padding: 0.65rem 0.75rem;
    background: var(--light);
  }
  .tgc-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.45rem;
  }
  .tgc-header h3 {
    margin: 0;
    font-size: 1rem;
  }
  .tgc-actions {
    display: flex;
    gap: 0.35rem;
    align-items: center;
  }
  .tgc-modal-btn, .tgc-open-link {
    font-family: var(--codeFont);
    font-size: 0.72rem;
    padding: 0.2rem 0.48rem;
    border-radius: 4px;
    border: 1px solid var(--lightgray);
    background: var(--light);
    color: var(--secondary);
    cursor: pointer;
    text-decoration: none;
  }
  .tgc-modal-btn:hover, .tgc-open-link:hover {
    border-color: var(--secondary);
  }
  .tgc-preview-wrap {
    width: 100%;
    height: 195px;
    border: 1px solid var(--lightgray);
    border-radius: 4px;
    overflow: hidden;
    cursor: pointer;
  }
  .tgc-mini-canvas {
    width: 100%;
    height: 100%;
    display: block;
  }
  .tgc-caption {
    margin-top: 0.4rem;
    font-size: 0.74rem;
    color: var(--gray);
    line-height: 1.35;
  }
  .tgc-dialog {
    width: min(96vw, 1520px);
    height: 91vh;
    padding: 0;
    border: 1px solid var(--gray);
    border-radius: 8px;
    background: var(--light);
    overflow: hidden;
  }
  .tgc-dialog::backdrop {
    background: rgba(15, 16, 20, 0.62);
  }
  .tgc-dialog-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.4rem 0.85rem;
    border-bottom: 1px solid var(--lightgray);
    font-family: var(--codeFont);
    font-size: 0.78rem;
    background: var(--light);
    color: var(--dark);
  }
  .tgc-dialog-close {
    font-family: var(--codeFont);
    font-size: 0.75rem;
    border: 1px solid var(--lightgray);
    background: transparent;
    color: var(--dark);
    padding: 0.2rem 0.55rem;
    border-radius: 4px;
    cursor: pointer;
  }
  .tgc-iframe {
    width: 100%;
    height: calc(91vh - 36px);
    border: none;
    display: block;
  }
  `

  Component.afterDOMLoaded = `
  document.querySelectorAll('.triomnibus-graph-card').forEach(async (card) => {
    const slug = card.getAttribute('data-slug') || 'index';
    const graphUrl = card.getAttribute('data-graph-url');
    const obsUrl = card.getAttribute('data-obs-url');
    const dialog = card.querySelector('.tgc-dialog');
    const iframe = card.querySelector('.tgc-iframe');
    const openModal = () => {
      if (iframe && !iframe.getAttribute('src')) iframe.setAttribute('src', obsUrl);
      if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
    };
    card.querySelector('.tgc-modal-btn')?.addEventListener('click', openModal);
    card.querySelector('.tgc-preview-wrap')?.addEventListener('click', openModal);
    card.querySelector('.tgc-dialog-close')?.addEventListener('click', () => dialog?.close());

    // Render crisp typed 1-hop ego preview on mini canvas
    const cv = card.querySelector('.tgc-mini-canvas');
    if (!cv || !graphUrl) return;
    try {
      const data = await fetch(graphUrl).then((r) => r.json());
      const center = data.nodes.find((n) => n.slug === slug) || data.nodes.find((n) => n.id === 'week:1') || data.nodes[0];
      if (!center) return;
      const nbs = [];
      const nodeMap = new Map(data.nodes.map((n) => [n.id, n]));
      for (const e of data.edges) {
        if (e.kind === 'sequence') continue;
        if (e.source === center.id && nodeMap.has(e.target)) nbs.push(nodeMap.get(e.target));
        else if (e.target === center.id && nodeMap.has(e.source)) nbs.push(nodeMap.get(e.source));
      }
      const uniq = Array.from(new Map(nbs.map((n) => [n.id, n])).values()).slice(0, 14);
      const dpr = window.devicePixelRatio || 1;
      const w = cv.clientWidth || 260;
      const h = cv.clientHeight || 195;
      cv.width = w * dpr;
      cv.height = h * dpr;
      const ctx = cv.getContext('2d');
      ctx.scale(dpr, dpr);
      const cx = w / 2, cy = h / 2, R = Math.min(w, h) * 0.36;
      const colors = { set: '#284b63', author_week: '#b55239', book: '#3d6987', person: '#48786d', theme: '#a87620', ghost_book: '#7656ad' };
      uniq.forEach((nb, i) => {
        const a = (i / Math.max(1, uniq.length)) * Math.PI * 2 - Math.PI / 2;
        const nx = cx + Math.cos(a) * R, ny = cy + Math.sin(a) * R;
        ctx.strokeStyle = 'rgba(130,140,150,0.35)';
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(nx, ny); ctx.stroke();
        ctx.fillStyle = colors[nb.type] || '#284b63';
        ctx.beginPath(); ctx.arc(nx, ny, 4.5, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--darkgray') || '#444';
        ctx.font = '10px Georgia, serif';
        const lbl = nb.title.length > 18 ? nb.title.slice(0, 16) + '…' : nb.title;
        ctx.fillText(lbl, nx + (Math.cos(a) >= 0 ? 7 : -ctx.measureText(lbl).width - 7), ny + 3);
      });
      ctx.fillStyle = '#b55239';
      ctx.beginPath(); ctx.arc(cx, cy, 7, 0, Math.PI * 2); ctx.fill();
    } catch (_) {}
  });
  `

  return Component
}
