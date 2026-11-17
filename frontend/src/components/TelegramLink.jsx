// External t.me link; anything that is not http(s) renders as plain text (the permalinks come from stored posts).
export const isSafeHref = (href) => typeof href === 'string' && /^https?:\/\//i.test(href);
export default function TelegramLink({ href, className = 'text-forest-800 underline underline-offset-2', children, ...rest }) {
  return isSafeHref(href)
    ? <a className={`break-words ${className}`} href={href} target="_blank" rel="noopener noreferrer" {...rest}>{children}</a>
    : <span>{children}</span>;
}
