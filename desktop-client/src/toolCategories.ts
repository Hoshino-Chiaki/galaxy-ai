const labels: Record<string, string> = { general: '通用', task: '任务系统', jushuitan: '聚水潭' }

export const toolCategoryLabel = (category: string) => labels[category] ?? category
