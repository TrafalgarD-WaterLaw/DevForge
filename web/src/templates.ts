// web/src/templates.ts — 任务模板（框架型）：用户只填关键字段，自动组装任务描述
export interface TemplateField {
  key: string
  label: string
  placeholder: string
  default?: string
}
export interface TaskTemplate {
  id: string
  icon: string
  label: string
  intro: string          // 框架说明（选类型后展示）
  fields: TemplateField[]
  build: (v: Record<string, string>) => string
}

export const TASK_TEMPLATES: TaskTemplate[] = [
  {
    id: 'cli',
    icon: '🖥',
    label: 'CLI 工具',
    intro: '命令行工具：处理输入并输出结果，支持命令行选项',
    fields: [
      { key: 'name', label: '工具名称', placeholder: '如：字数统计器', default: '字数统计器' },
      { key: 'input', label: '输入内容', placeholder: '如：一个或多个文本文件', default: '一个或多个文本文件' },
      { key: 'features', label: '核心功能', placeholder: '如：统计行数、单词数、字符数', default: '统计每个文件的行数、单词数、字符数（忽略空白字符）' },
      { key: 'options', label: '命令行选项', placeholder: '如：-l/-w/-c 与帮助信息', default: '-l/-w/-c 选项与帮助信息' },
    ],
    build: (v) =>
      `设计一个命令行工具「${v.name || 'CLI 工具'}」：${v.features || '处理输入数据'}。` +
      `输入${v.input || '文件'}，支持${v.options || '命令行选项'}。`,
  },
  {
    id: 'file',
    icon: '🗂',
    label: '文件工具',
    intro: '文件整理工具：扫描目录并按规则整理文件',
    fields: [
      { key: 'name', label: '工具名称', placeholder: '如：文件整理器', default: '文件整理器' },
      { key: 'action', label: '整理动作', placeholder: '如：按扩展名归类到子目录', default: '按扩展名把文件移动到对应子目录' },
      { key: 'extra', label: '附加能力', placeholder: '如：预览模式、排除规则', default: '支持 --dry-run 预览模式与排除指定扩展名' },
    ],
    build: (v) =>
      `设计一个命令行${v.name || '文件整理工具'}：扫描指定目录，` +
      `${v.action || '按规则整理文件'}，${v.extra || '支持预览模式'}。`,
  },
  {
    id: 'data',
    icon: '📊',
    label: '数据处理',
    intro: '数据处理：读取数据文件，输出统计与报告',
    fields: [
      { key: 'format', label: '数据格式', placeholder: '如：CSV 文件', default: 'CSV 文件' },
      { key: 'stats', label: '统计项', placeholder: '如：每列均值、中位数、缺失值', default: '每列均值、中位数、缺失值数量' },
      { key: 'output', label: '输出形式', placeholder: '如：汇总报告', default: '汇总报告' },
    ],
    build: (v) =>
      `开发一个${v.format || '数据'}分析脚本：读取数据文件，输出${v.stats || '统计信息'}，` +
      `并生成${v.output || '汇总报告'}。`,
  },
]
