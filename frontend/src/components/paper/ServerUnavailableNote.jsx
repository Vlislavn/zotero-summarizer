import useUnavailableText from '../../hooks/useUnavailableText.js';
import { syncNow } from '../../syncClient.js';

// Shown where a server-only control would be; wording rule in useUnavailableText.
export default function ServerUnavailableNote({ what, children }) {
  const { busy, say } = useUnavailableText();
  return (
    <p className="text-xs text-slate-500">
      {say(`${what} need the Mac.`)}
      {busy && <> <button type="button" onClick={() => syncNow()} className="font-semibold text-teal-700">Check now</button></>}
      {children}
    </p>
  );
}
