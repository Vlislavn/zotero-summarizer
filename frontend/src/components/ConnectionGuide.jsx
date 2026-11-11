import { useEffect, useState } from 'react';

// Short iPhone recipe: the app is reached over the owner's Tailscale HTTPS address
// (`make iphone`); that same address is what gets installed and what Sync returns to.
export default function ConnectionGuide() {
  const [shellReady, setShellReady] = useState(Boolean(navigator.serviceWorker?.controller));
  useEffect(() => {
    const update = () => setShellReady(Boolean(navigator.serviceWorker?.controller));
    navigator.serviceWorker?.addEventListener('controllerchange', update);
    return () => navigator.serviceWorker?.removeEventListener('controllerchange', update);
  }, []);
  return (
    <details>
      <summary className="cursor-pointer min-h-11 flex items-center text-slate-600">Install &amp; offline use</summary>
      <div className="pb-2 space-y-2 text-slate-600 max-w-prose">
        <p>{!window.isSecureContext
          ? 'This address cannot install an offline app. Open the https:// Tailscale address that `make iphone` prints on the Mac.'
          : shellReady ? 'Offline shell is active.' : 'Offline shell not installed yet — reload once while connected.'}</p>
        <ol className="list-decimal pl-5 space-y-1">
          <li>In Safari with Tailscale on, open this address, then Share → Add to Home Screen.</li>
          <li>While connected, sync the papers you need.</li>
          <li>Mac off or out of reach: open cached papers from Library; verdicts and notes stay on this device. Don’t clear Safari site data.</li>
          <li>Back online: open this same address and tap Sync now.</li>
        </ol>
        <p>PDFs, figures, AI and rescoring need the Mac. A sync acknowledgement is not proof that Zotero has the change — Ops → Pending changes shows what is still queued.</p>
      </div>
    </details>
  );
}
