import { describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import { createFeed } from '../feed'
import type { FeedItem } from '../feed'

describe('vitest infra smoke', () => {
  it('基础设施可用：createFeed 处理真实 wire 事件并产出 question 条目', () => {
    const feed = createFeed({ items: reactive<FeedItem[]>([]) })
    feed.addEvent({
      event: 'discuss_choice',
      question: '开发什么？',
      options: ['A', 'B'],
      allow_multiple: false,
    })
    expect(feed.items).toHaveLength(1)
    const q = feed.items[0]
    expect(q.type).toBe('question')
    expect(q.question?.options).toEqual(['A', 'B'])
    expect(q.question?.allowMultiple).toBe(false)
  })
})
