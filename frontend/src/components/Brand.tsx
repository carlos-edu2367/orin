import { Link } from 'react-router-dom'
import { BRAND_LOGO_PATH, BRAND_NAME } from '../config/brand'

type Props = {
  to?: string
  href?: string
  ariaLabel?: string
}

export function Brand({ to, href, ariaLabel = `${BRAND_NAME}, início` }: Props) {
  const content = <>
    <span className="brand__mark" aria-hidden="true"><img src={BRAND_LOGO_PATH} alt="" /></span>
    <span className="brand__word">{BRAND_NAME}</span>
  </>

  if (to) return <Link className="brand" to={to} aria-label={ariaLabel}>{content}</Link>
  if (href) return <a className="brand" href={href} aria-label={ariaLabel}>{content}</a>
  return <span className="brand" aria-label={ariaLabel}>{content}</span>
}
