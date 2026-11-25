const DAY = 24 * 60 * 60 * 1000
// The chip warns when 3 days are left or 15% of the token's lifetime, whichever comes first.
const WARN_MIN_LEFT = 3 * DAY
const WARN_SHARE = 0.15

// The chip's text when the token is near its expiry or has expired, else null.
export function expiryWarning(expiresAt: string | null, createdAt: string, now: number): string | null {
  if (expiresAt === null) {
    return null
  }
  const expires = new Date(expiresAt).getTime()
  const left = expires - now
  if (Number.isNaN(left)) {
    return null
  }
  if (left <= 0) {
    return 'MCP token expired'
  }
  const lifetime = expires - new Date(createdAt).getTime()
  const share = Number.isNaN(lifetime) ? 0 : lifetime * WARN_SHARE
  if (left > Math.max(WARN_MIN_LEFT, share)) {
    return null
  }
  const days = Math.ceil(left / DAY)
  return `MCP token expires in ${days} ${days === 1 ? 'day' : 'days'}`
}
