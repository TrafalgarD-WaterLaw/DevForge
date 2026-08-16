// Workaround: Edge bug — replaceState when page hidden forces window to foreground.
// Intercept and defer replaceState calls while document is hidden.
const _origReplaceState = window.history.replaceState.bind(window.history)
window.history.replaceState = function (state: any, title: string, url?: string | null) {
  if (document.hidden) {
    // Defer to next idle moment so Edge doesn't steal focus
    requestAnimationFrame(() => {
      _origReplaceState(state, title, url)
    })
    return
  }
  return _origReplaceState(state, title, url)
}

import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Dashboard from './views/Dashboard.vue'
import History from './views/History.vue'
import ProjectDetail from './views/ProjectDetail.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Dashboard },
    { path: '/history', component: History },
    { path: '/project/:id', component: ProjectDetail },
  ],
})
createApp(App).use(router).mount('#app')
