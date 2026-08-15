import { Link } from 'react-router-dom'
import type { SkillSummary } from '../../api/skills'

export function SkillRow({ skill, basePath = '/settings/skills' }: { skill: SkillSummary; basePath?: string }) {
  return <Link className="skill-row" to={`${basePath}/${encodeURIComponent(skill.id)}`}><span className="skill-row__main"><strong>{skill.name}</strong><span>{skill.description}</span></span><span className="skill-row__meta"><code>v{skill.version}</code><span>{skill.source}</span><span aria-label={skill.available ? 'Disponível' : 'Indisponível'} className={skill.available ? 'skill-row__availability is-available' : 'skill-row__availability'}>{skill.available ? 'Disponível' : 'Indisponível'}</span></span></Link>
}
