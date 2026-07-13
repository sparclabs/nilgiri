import { getHomePermalink, getPermalink } from './utils/permalinks';

const GITHUB_REPO = 'https://github.com/sparclabs/nilgiri';
const BLOG_POST = 'https://www.sparc-labs.in/resources/introducing-nilgiri/';
const SPARC_LABS = 'https://www.sparc-labs.in';

export const headerData = {
  links: [
    {
      text: 'Home',
      href: getHomePermalink(),
    },
    {
      text: 'Leaderboard',
      href: getPermalink('/leaderboard'),
    },
    {
      text: 'Example Task',
      href: getPermalink('/example-task'),
    },
    {
      text: 'Contribute',
      href: getPermalink('/contribute'),
    },
    {
      text: 'Blog',
      href: BLOG_POST,
    },
  ],
  actions: [{ text: 'GitHub', href: GITHUB_REPO, target: '_blank', icon: 'tabler:brand-github' }],
};

export const footerData = {
  links: [
    {
      title: 'Site',
      links: headerData.links,
    },
    {
      title: 'Data & reproducibility',
      links: [
        { text: 'results.json', href: `${GITHUB_REPO}/blob/main/docs/leaderboard/results.json` },
        { text: 'schema.json', href: `${GITHUB_REPO}/blob/main/docs/leaderboard/schema.json` },
        { text: 'flags/manifest.yaml', href: `${GITHUB_REPO}/blob/main/flags/manifest.yaml` },
      ],
    },
    {
      title: 'Related work',
      links: [
        { text: 'UK AISI "The Last Ones" (arXiv 2603.11214)', href: 'https://arxiv.org/abs/2603.11214' },
        { text: 'GOAD (Game of Active Directory)', href: 'https://github.com/Orange-Cyberdefense/GOAD' },
        { text: 'Inspect AI', href: 'https://inspect.aisi.org.uk/' },
      ],
    },
  ],
  secondaryLinks: [{ text: 'MIT License', href: `${GITHUB_REPO}/blob/main/LICENSE` }],
  socialLinks: [
    { ariaLabel: 'GitHub', icon: 'tabler:brand-github', href: GITHUB_REPO },
    { ariaLabel: 'SPARC Labs', icon: 'tabler:building-fortress', href: SPARC_LABS },
  ],
  footNote: `
    Built and maintained by <a class="text-primary underline" href="${SPARC_LABS}" target="_blank" rel="noopener">SPARC Labs</a> · Research infrastructure for AI safety evals only · Released under the MIT License.
  `,
};
