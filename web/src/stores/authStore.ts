/**
 * 鉴权状态（zustand + localStorage）。
 *
 * ⚠️ 这里只存**令牌与用户**，不存业务数据 —— 业务数据一律归 React Query
 * （契约与 ADR-004 的分工：服务端状态用 React Query，客户端状态用 zustand）。
 *
 * 令牌过期**无法续期**：后端没有 refresh 端点（`api/v1/auth.py` 只发
 * `access_token` + `expires_in`），所以 401 的唯一出路是重新登录。
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import type { TokenOut, UserOut } from '../api/types'

interface AuthState {
  token: string | null
  user: UserOut | null
  setSession: (token: TokenOut) => void
  setUser: (user: UserOut) => void
  logout: () => void
  isAuthenticated: () => boolean
}

export const AUTH_STORAGE_KEY = 'comfyui-platform.auth'

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,

      setSession: (token) => set({ token: token.access_token }),

      setUser: (user) => set({ user }),

      logout: () => set({ token: null, user: null }),

      isAuthenticated: () => Boolean(get().token),
    }),
    {
      name: AUTH_STORAGE_KEY,
      // 只持久化令牌：`user` 每次启动用 `/auth/me` 重新拉，
      // 否则配额等信息会在本地陈旧（配额是本产品最敏感的数字之一）。
      partialize: (state) => ({ token: state.token }) as unknown as AuthState,
    },
  ),
)
