<template>
  <div class="hist">
    <div class="hero"><h1>History</h1><p>Previously generated projects.</p></div>
    <div v-if="loading" class="dim">Loading...</div>
    <div v-else-if="!projects.length" class="card"><p class="dim">Nothing yet. Run a task first.</p></div>
    <div v-else>
      <div v-for="p in projects" :key="p.id" class="card row" @click="open(p.id)">
        <div><div class="nm">{{ p.name }}</div><div class="mt">{{ p.files.length }} files &middot; {{ new Date(p.created*1000).toLocaleDateString() }}</div></div>
        <span class="ar">&rarr;</span>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">import{ref,onMounted}from'vue';import{useRouter}from'vue-router';import{apiFetch}from'../api'
const r=useRouter();const projects=ref<any[]>([]);const loading=ref(true)
// 走 apiFetch：带 X-Auth-Token 鉴权头（裸 fetch 在启用 token 后 401 静默空白）
onMounted(async()=>{try{const res=await apiFetch('/api/projects');projects.value=(await res.json()).projects||[]}catch{}finally{loading.value=false}})
function open(id:string){r.push(`/project/${id}`)}
</script>
<style scoped>
.hist{max-width:880px;padding:32px 24px}.hero{margin-bottom:24px}.hero h1{font-size:24px;font-weight:700;color:var(--text);letter-spacing:-0.4px}.hero p{color:var(--dim);margin-top:6px;font-size:14px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px 22px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.row{display:flex;align-items:center;justify-content:space-between;cursor:pointer}.row:hover{border-color:var(--accent)}
.nm{font-weight:600;color:var(--text);font-size:14px}.mt{font-size:12px;color:var(--dim);margin-top:3px}.ar{font-size:18px;color:var(--dim)}.dim{color:var(--dim);font-size:13px}
</style>
