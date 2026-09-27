// Plain-language "what this marker is" text for the Health page's blood-test charts. General
// educational descriptions only -- what a marker measures, never a target, a "normal range", or an
// interpretation of the athlete's own value (this app's standing rule: the only range it ever shows
// is the one printed on the athlete's own lab report). Keyed by lower-cased marker name; a marker
// with no entry here simply shows no description.
const INFO: Record<string, string> = {
  // Lipids
  "total cholesterol":
    "All the cholesterol carried in the blood, in HDL, LDL and other particles combined. Cholesterol is a fat-like substance the body uses to build cells and hormones.",
  "hdl cholesterol":
    "Cholesterol carried by high-density lipoproteins, often called the “good” cholesterol because these particles carry cholesterol back to the liver for disposal.",
  "ldl cholesterol":
    "Cholesterol carried by low-density lipoproteins, the main particles that deposit cholesterol in artery walls.",
  "ldl cholesterol (calc)":
    "Cholesterol carried by low-density lipoproteins, the main particles that deposit cholesterol in artery walls. Calculated from the other lipid results (Friedewald equation) rather than measured directly.",
  triglycerides:
    "The main form of fat in the blood, coming from food and made by the liver. Levels rise after eating, so it is normally measured fasting.",
  "cholesterol/hdl ratio":
    "Total cholesterol divided by HDL cholesterol. A quick way of comparing the amount of cholesterol in circulation with the amount being carried away.",
  "vldl cholesterol (calc)":
    "Cholesterol in very-low-density lipoproteins, the particles that carry triglycerides. Estimated from the triglyceride result rather than measured directly.",
  "non-hdl cholesterol":
    "Total cholesterol minus HDL: all the cholesterol in the particles other than HDL (LDL, VLDL and others).",

  // Glucose & thyroid
  glucose:
    "Sugar in the blood, the body's main fuel. A fasting sample shows how well blood sugar is regulated without a recent meal.",
  hba1c:
    "Glycated hemoglobin: the share of hemoglobin that has sugar attached, reflecting average blood sugar over roughly the previous three months.",
  "estimated average glucose":
    "The average blood glucose implied by the HbA1c result, calculated from it with a standard formula.",
  tsh:
    "Thyroid-stimulating hormone, made by the pituitary gland to tell the thyroid how much hormone to produce. It is the usual first screen of thyroid function.",

  // Electrolytes & kidney
  sodium:
    "The main electrolyte outside cells, controlling fluid balance and blood pressure and needed for nerve and muscle signals.",
  potassium:
    "An electrolyte mostly held inside cells, essential for heart rhythm and muscle contraction. The kidneys keep the blood level tightly controlled.",
  chloride:
    "An electrolyte that works with sodium to keep fluid and acid-base balance in the body.",
  "co2 (bicarbonate)":
    "Mostly bicarbonate, the blood's main buffer against acidity. It reflects the body's acid-base balance.",
  "anion gap":
    "A calculated difference between the measured positive and negative ions in the blood (sodium minus chloride and bicarbonate), used to look for unmeasured acids.",
  bun:
    "Blood urea nitrogen: a waste product from protein breakdown that the kidneys clear. Also affected by hydration and protein intake.",
  creatinine:
    "A waste product from normal muscle activity, filtered out by the kidneys. Blood levels depend on kidney function and on muscle mass.",
  egfr:
    "Estimated glomerular filtration rate: an estimate of how much blood the kidneys filter per minute, calculated from creatinine, age and sex.",
  calcium:
    "A mineral needed for bones, muscles, nerves and blood clotting. Most is in bone; the blood level is kept within a narrow band.",
  magnesium:
    "A mineral involved in hundreds of enzyme reactions, including muscle, nerve and heart function.",

  // Liver & pancreas
  alt:
    "Alanine aminotransferase: an enzyme found mainly in the liver that leaks into the blood when liver cells are irritated or damaged.",
  ast:
    "Aspartate aminotransferase: an enzyme found in the liver but also in muscle and the heart, so hard exercise can raise it as well.",
  "alkaline phosphatase":
    "An enzyme found in the liver, bile ducts and bone. Results are read alongside other liver tests.",
  "total bilirubin":
    "A yellow pigment made when old red blood cells are broken down, processed by the liver and passed out in bile.",
  "direct bilirubin":
    "The part of bilirubin that the liver has already processed and made water-soluble, on its way into bile.",
  albumin:
    "The most abundant protein in blood plasma, made by the liver. It keeps fluid in the blood vessels and carries hormones, minerals and other substances.",
  "total protein":
    "All the protein in the blood, mainly albumin and globulins. A broad indicator of nutrition and liver and kidney function.",
  globulin:
    "A group of blood proteins including antibodies, carrier proteins and clotting factors. Calculated as total protein minus albumin.",
  lipase:
    "A digestive enzyme made mostly by the pancreas to break down fats. Blood levels rise when the pancreas is inflamed.",

  // Blood count
  wbc:
    "White blood cell count: the cells of the immune system that fight infection. The count rises with infection, inflammation and physical stress.",
  rbc:
    "Red blood cell count: the number of oxygen-carrying cells in a given volume of blood.",
  hemoglobin:
    "The iron-containing protein in red blood cells that carries oxygen from the lungs to the tissues.",
  hematocrit:
    "The percentage of blood volume taken up by red blood cells.",
  mcv:
    "Mean corpuscular volume: the average size of a red blood cell.",
  mch:
    "Mean corpuscular hemoglobin: the average amount of hemoglobin in one red blood cell.",
  mchc:
    "Mean corpuscular hemoglobin concentration: how densely hemoglobin is packed inside red blood cells.",
  rdw:
    "Red cell distribution width: how much red blood cells vary in size from one another.",
  platelets:
    "Small cell fragments that stick together to form clots and stop bleeding.",
  "neutrophils %":
    "Neutrophils as a share of all white blood cells. Neutrophils are the first-responder cells that fight bacterial infection.",
  "lymphocytes %":
    "Lymphocytes as a share of all white blood cells. They include the T cells and B cells that drive immune memory and antibodies.",
  "monocytes %":
    "Monocytes as a share of all white blood cells. They clear debris and mature into tissue macrophages.",
  "eosinophils %":
    "Eosinophils as a share of all white blood cells. They respond to allergies and parasites.",
  "basophils %":
    "Basophils as a share of all white blood cells. The rarest type, involved in allergic reactions.",
  "immature granulocytes %":
    "Young white blood cells released from the bone marrow before fully maturing, as a share of all white blood cells. Higher during infection or stress.",
  "neutrophils (abs)":
    "The absolute number of neutrophils per volume of blood, the first-responder cells against bacterial infection.",
  "lymphocytes (abs)":
    "The absolute number of lymphocytes per volume of blood (T cells, B cells and natural killer cells).",
  "monocytes (abs)":
    "The absolute number of monocytes per volume of blood.",
  "eosinophils (abs)":
    "The absolute number of eosinophils per volume of blood, the cells involved in allergy and parasite responses.",
  "basophils (abs)":
    "The absolute number of basophils per volume of blood, the rarest white cell type.",
  "immature granulocytes (abs)":
    "The absolute number of young white blood cells released early from the bone marrow.",

  // Blood gas
  "venous ph":
    "The acidity of venous blood, on a scale where lower is more acidic. The body keeps it in a very narrow band.",
  "venous pco2":
    "The pressure of carbon dioxide dissolved in venous blood, reflecting how well the lungs clear it.",
  "venous po2":
    "The pressure of oxygen dissolved in venous blood. It is much lower than in arterial blood because the tissues have taken oxygen up.",
  "venous hco3":
    "Bicarbonate in venous blood, the main buffer against acid, as calculated by the blood-gas analyzer.",
  "venous tco2":
    "Total carbon dioxide in venous blood: bicarbonate plus dissolved CO2.",
  "base excess":
    "How much acid or base would be needed to bring the blood back to a normal pH. It shows the metabolic (non-breathing) part of acid-base balance.",
  "venous o2 saturation":
    "The percentage of hemoglobin carrying oxygen in venous blood, after the tissues have taken their share.",
  lactate:
    "A by-product of energy production without enough oxygen, and of intense exercise. High levels can signal that tissues are not getting enough oxygen.",

  // Common names the add-form offers that are not spelled the way the lab reports do
  "vitamin d":
    "A hormone-like vitamin made in the skin from sunlight and taken from food, needed for calcium absorption and bone health.",
  "vitamin b12":
    "A vitamin needed for nerve function and making red blood cells, found in animal foods.",
  ferritin:
    "A protein that stores iron; the blood level is the usual indicator of the body's iron stores.",
  iron:
    "The mineral that hemoglobin uses to carry oxygen. The blood level varies through the day and with recent meals.",
  crp:
    "C-reactive protein: made by the liver in response to inflammation, so it rises with infection and other inflammatory conditions.",
  "free t4":
    "The active, unbound form of thyroxine, the main hormone made by the thyroid gland to set the body's metabolic rate.",
  "white blood cell count":
    "The number of white blood cells, the immune-system cells that fight infection.",
  "platelet count":
    "The number of platelets, the cell fragments that form clots and stop bleeding.",
  "glucose (fasting)":
    "Sugar in the blood after not eating for several hours, showing how well blood sugar is regulated at rest.",
};

/** A short description of what the marker measures, or null when there is none for that name. */
export function markerDescription(marker: string): string | null {
  return INFO[marker.trim().toLowerCase()] ?? null;
}
