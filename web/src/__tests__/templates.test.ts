import { describe, expect, it } from 'vitest'
import { TASK_TEMPLATES } from '../templates'

describe('任务模板（框架型）', () => {
  it('包含三种类型且都有字段', () => {
    const ids = TASK_TEMPLATES.map((t) => t.id)
    expect(ids).toEqual(['cli', 'file', 'data'])
    for (const t of TASK_TEMPLATES) {
      expect(t.fields.length).toBeGreaterThan(0)
    }
  })

  it('CLI 模板：填入值组装进任务描述', () => {
    const cli = TASK_TEMPLATES.find((t) => t.id === 'cli')!
    const desc = cli.build({ name: '日志分析器', input: '日志文件', features: '统计错误数', options: '-e 选项' })
    expect(desc).toContain('日志分析器')
    expect(desc).toContain('日志文件')
    expect(desc).toContain('统计错误数')
    expect(desc).toContain('-e 选项')
  })

  it('文件工具模板：默认值兜底（空字段不产生空洞描述）', () => {
    const file = TASK_TEMPLATES.find((t) => t.id === 'file')!
    const desc = file.build({})
    expect(desc).toContain('文件整理')
    expect(desc).toContain('扫描')
    expect(desc).toContain('预览模式')
  })

  it('数据处理模板：按字段组装', () => {
    const data = TASK_TEMPLATES.find((t) => t.id === 'data')!
    const desc = data.build({ format: 'JSON 文件', stats: '字段缺失率', output: 'HTML 报告' })
    expect(desc).toContain('JSON 文件')
    expect(desc).toContain('字段缺失率')
    expect(desc).toContain('HTML 报告')
  })
})
