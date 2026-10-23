const CUR = 2026;
function computeBatch(grade, enrollmentYear) {
  var g = parseInt(grade, 10);
  var en = parseInt(enrollmentYear, 10);
  if (isNaN(g)) return null;
  if (isNaN(en)) {
    var b0 = CUR - g + 1;
    return (b0 >= 1 && b0 <= 3) ? b0 : null;
  }
  var startY;
  if (g === en) startY = g;
  else if (g < en) startY = g;
  else startY = (g > CUR) ? en : g;
  var b = CUR - startY + 1;
  return (b >= 1 && b <= 3) ? b : null;
}
const cases = [
  ["普通 2024/2024", 2024, 2024, 3],
  ["普通 2026/2026", 2026, 2026, 1],
  ["转入 2025/2026", 2025, 2026, 2],
  ["转入 2024/2025", 2024, 2025, 3],
  ["特殊未来 2027/2026", 2027, 2026, 1],
  ["特殊未来 2028/2026", 2028, 2026, 1],
  ["特殊合法 2024/2023", 2024, 2023, 3],
  ["特殊合法 2026/2025", 2026, 2025, 1],
  ["无入学 2024/None", 2024, "", 3],
  ["年级空", "", 2026, null],
  ["溢出 2023/2023", 2023, 2023, null],
];
let ok = true;
for (const [name, g, e, exp] of cases) {
  const got = computeBatch(g, e);
  const mark = got === exp ? "OK" : "FAIL";
  if (got !== exp) ok = false;
  console.log("  [%s] %-22s -> %s (期望 %s)", mark, name, got, exp);
}
// 张东旭真实值
console.log("  张东旭 2024/2023 ->", computeBatch(2024, 2023), "(应 3)");
console.log("\n前端JS逻辑全部通过:", ok ? "YES" : "NO");
