const USER_KEY = 'pentest_copilot_user'

export function getUser() {
  try {
    const user = localStorage.getItem(USER_KEY)
    if (!user || user === 'undefined' || user === 'null') {
      return { username: 'user', role: 'admin' }
    }
    return JSON.parse(user)
  } catch {
    return { username: 'user', role: 'admin' }
  }
}
