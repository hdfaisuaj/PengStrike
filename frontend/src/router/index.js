import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    component: () => import('../layouts/MainLayout.vue'),
    meta: { requiresAuth: false },
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('../views/Dashboard.vue')
      },
      {
        path: 'sessions',
        name: 'SessionList',
        component: () => import('../views/SessionList.vue')
      },
      {
        path: 'sessions/:id',
        name: 'SessionDetail',
        component: () => import('../views/SessionDetail.vue')
      },
      {
        path: 'tools',
        name: 'ToolList',
        component: () => import('../views/ToolList.vue')
      },
      {
        path: 'roles',
        name: 'RoleList',
        component: () => import('../views/RoleList.vue')
      },
      {
        path: 'reports',
        name: 'ReportList',
        component: () => import('../views/ReportList.vue')
      },
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('../views/Settings.vue')
      }
    ]
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
