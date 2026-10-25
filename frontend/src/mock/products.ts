export interface Product {
  sku: string; barcode: string; name: string; spec: string; category: string; brand: string; price: number; unit: string;
}

export const PRODUCTS: Product[] = [
  { sku: '1002387', barcode: '6901028075763', name: '农夫山泉饮用天然水', spec: '550ml×24瓶', category: '酒水饮料/饮用水', brand: '农夫山泉', price: 39.9, unit: '箱' },
  { sku: '1004512', barcode: '6920202888883', name: '金龙鱼黄金比例食用调和油', spec: '5L', category: '粮油调味/食用油', brand: '金龙鱼', price: 69.9, unit: '桶' },
  { sku: '1011846', barcode: '6907992513560', name: '伊利纯牛奶', spec: '250ml×16盒', category: '乳品/常温奶', brand: '伊利', price: 54.9, unit: '提' },
  { sku: '1013309', barcode: '6923644266318', name: '蒙牛特仑苏有机纯牛奶', spec: '250ml×12盒', category: '乳品/常温奶', brand: '蒙牛', price: 69.0, unit: '提' },
  { sku: '1020075', barcode: '6902083881085', name: '娃哈哈AD钙奶', spec: '220g×4瓶', category: '乳品/乳饮料', brand: '娃哈哈', price: 9.9, unit: '组' },
  { sku: '1024418', barcode: '6970399920019', name: '五常大米 稻花香2号', spec: '5kg', category: '粮油调味/大米', brand: '柴火大院', price: 79.8, unit: '袋' },
  { sku: '1031752', barcode: '6901236341285', name: '维达超韧抽纸', spec: '3层130抽×24包', category: '日化家清/纸品', brand: '维达', price: 59.9, unit: '提' },
  { sku: '1035590', barcode: '6903148042312', name: '蓝月亮深层洁净洗衣液', spec: '3kg', category: '日化家清/洗衣', brand: '蓝月亮', price: 45.9, unit: '瓶' },
  { sku: '1040281', barcode: '6941499107153', name: '三只松鼠每日坚果', spec: '750g（30袋）', category: '休闲食品/坚果', brand: '三只松鼠', price: 129.0, unit: '盒' },
  { sku: '1046637', barcode: '6911988014337', name: '海天金标生抽', spec: '1.28L', category: '粮油调味/酱油', brand: '海天', price: 16.8, unit: '瓶' },
  { sku: '2008814', barcode: '2800814000006', name: '国产红富士苹果（称重）', spec: '约1kg', category: '生鲜/水果', brand: '--', price: 12.98, unit: 'kg' },
  { sku: '2010437', barcode: '2810437000004', name: '冷鲜猪肋排（称重）', spec: '约500g', category: '生鲜/肉禽', brand: '--', price: 46.8, unit: 'kg' },
];
export const productBySku = (sku: string) => PRODUCTS.find((p) => p.sku === sku);
