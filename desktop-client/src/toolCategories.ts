const labels: Record<string, string> = { general: '通用', task: '任务系统' }

export const toolCategoryLabel = (category: string) => labels[category] ?? category
