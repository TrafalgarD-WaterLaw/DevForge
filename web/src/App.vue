<template>
  <div class="shell">
    <header class="topbar">
      <router-link to="/" class="brand">DevForge</router-link>
      <nav>
        <router-link to="/" class="nav-i">Forge</router-link>
        <router-link to="/history" class="nav-i">History</router-link>
      </nav>
    </header>
    <main class="main"><router-view /></main>
  </div>
</template>
<style>
:root{--bg:#F8F9FA;--card:#FFFFFF;--border:#E5E7EB;--text:#1A1D23;--dim:#6B7280;--accent:#3B82F6;--green:#10B981;--red:#EF4444}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body,#app{min-height:100vh}
.shell{min-height:100vh}
.topbar{display:flex;align-items:center;gap:32px;padding:0 32px;height:52px;background:var(--card);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10}
.brand{font-size:18px;font-weight:700;color:var(--text);text-decoration:none;letter-spacing:-0.3px}
.nav-i{padding:6px 14px;border-radius:6px;color:var(--dim);text-decoration:none;font-size:13px;font-weight:500;transition:all .15s}
.nav-i:hover{color:var(--text);background:#F3F4F6}
.nav-i.router-link-active{color:var(--accent);background:#EFF6FF;font-weight:600}
.main{width:100%;min-width:0;margin:0 auto;padding:0;box-sizing:border-box}
</style>
