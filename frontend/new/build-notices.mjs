import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { readFileSync, writeFileSync } from 'node:fs';
const require=createRequire(import.meta.url), blocks=[];
for (const name of ['react','react-dom','scheduler','tailwindcss','vite']) {
 const jsonPath=require.resolve(name+'/package.json',{paths:[dirname(require.resolve('react-dom/package.json'))]});
 const pkg=JSON.parse(readFileSync(jsonPath,'utf8'));
 let text;for(const filename of ['LICENSE','LICENSE.md','LICENSE.txt']){try{text=readFileSync(join(dirname(jsonPath),filename),'utf8');break;}catch{}}
 if(!text)throw new Error('Missing license '+name);
 blocks.push(name+' '+pkg.version+'\n'+text.trim());
}
writeFileSync('public/THIRD-PARTY-NOTICES.txt','Third-party licenses for the bundled web interface\n\n'+blocks.join('\n\n'+'='.repeat(72)+'\n\n')+'\n');
console.log('Bundled legal notices:', blocks.length);
