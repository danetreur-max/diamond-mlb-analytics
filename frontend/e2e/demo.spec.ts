import {test,expect} from '@playwright/test';
test('research journey: filter roster and inspect player evidence',async({page})=>{
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'What did the team get for its money?'})).toBeVisible();
 await page.getByLabel('Find a player').fill('Austin Riley');
 await expect(page.locator('#players tr')).toHaveCount(1);
 await page.getByRole('button',{name:'Inspect Austin Riley'}).click();
 await expect(page.locator('#report')).toContainText('Historical pace gap');
 await page.getByText('Inspect sources and caveats',{exact:true}).click();
 await expect(page.locator('#report')).toContainText('Contract as of');
 await expect(page.locator('#validation tr')).toHaveCount(4);
});
test('saved review persists and can be removed',async({page})=>{
 await page.goto('/');
 await page.getByRole('button',{name:'Save player review',exact:true}).click();
 await expect(page.locator('#status')).toContainText('Review saved');
 await page.reload();
 await expect(page.getByRole('button',{name:'Update saved review'})).toBeVisible();
 await page.getByLabel('Saved reviews only').check();
 await expect(page.locator('#players tr')).toHaveCount(1);
 await page.getByRole('button',{name:'Remove saved review'}).click();
 await expect(page.locator('#players tr')).toHaveCount(0);
});
test('missing data and service failure have explicit states',async({page})=>{
 await page.route('**/snapshot.json',route=>route.fulfill({status:503,body:'Unavailable'}));
 await page.goto('/');
 await expect(page.locator('#status')).toContainText('Unable to load');
});
test('small screens keep the document within the viewport',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/');
 await expect(page.locator('#players tr').first()).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
});
